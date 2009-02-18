# This file is part of Moksha.
#
# Moksha is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Moksha is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Moksha.  If not, see <http://www.gnu.org/licenses/>.
#
# Copyright 2008, Red Hat, Inc.
# Authors: Luke Macken <lmacken@redhat.com>

import moksha
import logging
import feedparser, time, sys
import md5, os

try:
    from cStringIO import cStringIO as StringIO
except ImportError:
    from StringIO import StringIO

from twisted.web import client
from twisted.web.client import HTTPPageGetter, HTTPClientFactory
from twisted.internet import reactor, protocol, defer, ssl
from twisted.web import error
from twisted.internet import protocol, defer

from datetime import timedelta, datetime
from feedcache import Cache
from shove import Shove
from tg import config

from moksha.hub import MokshaHub
from moksha.hub.http import conditional_get_page
from moksha.api.streams import PollingDataStream

log = logging.getLogger('moksha.hub')

feed_storage = Shove(config['feed_cache'], compress=True)
feed_cache = Cache(feed_storage)

class ConditionalHTTPPageGetter(HTTPPageGetter):

    def handleStatus_200(self):
        """ Attempt to save the last-modified header """
        if self.headers.has_key('last-modified'):
            self.factory.lastModified(self.headers['last-modified'])

    def handleStatus_304(self):
        """ Close the connection """
        self.factory.notModified()
        self.transport.loseConnection()


class ConditionalHTTPClientFactory(HTTPClientFactory):

    protocol = ConditionalHTTPPageGetter

    def __init__(self, url, method='GET', postdata=None, headers=None,
                 agent=None, timeout=0, cookies=None, followRedirect=1):

        self.url = url

        if url in feed_storage:
            lastModified = time.ctime(feed_storage[url][0])
            if headers is not None:
                headers['last-modified'] = lastModified
            else:
                headers = {'last-modified': lastModified}

        HTTPClientFactory.__init__(self, url, method=method, postdata=postdata,
                headers=headers, agent=agent, timeout=timeout, cookies=cookies,
                followRedirect=followRedirect)

        self.waiting = True
        self.deferred = defer.Deferred()

    def lastModified(self, modtime):
        t = time.mktime(time.strptime(modtime[0], '%a, %d %b %Y %H:%M:%S %Z'))
        parsed_feed = {}

        if self.url in feed_storage:
            current_feed = feed_storage[self.url][1]
            if current_feed and not current_feed.get('bozo_exception'):
                parsed_feed = current_feed

        try:
            feed_storage[self.url] = (t, parsed_feed)
        except Exception, e:
            log.error("Unable to store parsed_feed: %r" % parsed_feed)
            log.exception(e)

    def notModified():
        if self.waiting:
            self.waiting = False


def conditional_get_page(url, contextFactory=None, *args, **kwargs):
    scheme, host, port, path = client._parse(url)
    factory = ConditionalHTTPClientFactory(url, *args, **kwargs)
    if scheme == 'https':
        if contextFactory is None:
            contextFactory = ssl.ClientContextFactory()
        reactor.connectSSL(host, port, factory, contextFactory)
    else:
        reactor.connectTCP(host, port, factory)
    return factory.deferred


class FeederProtocol(object):

    max_age = int(config.get('feed.max_age', 300))
    timeout = int(config.get('feed.timeout', 30))

    def __init__(self):
        self.parsed = 1
        self.hub = MokshaHub()

    def is_cached(self, site):
        already_got = feed_storage.get(site)
        if already_got:
            elapsed_time = time.time() - already_got[0]
            if elapsed_time < self.max_age:
                return True
            else:
                return False
        else:
            return False

    def on_error(self, traceback, extra_args):
        log.error(extra_args)
        log.exception(traceback)

    def get_feed_from_cache(self, data, key=None):
        """ Return feed data from the cache based on a given ``key`` """
        log.debug('Getting cached feed for %s' % key)
        return defer.succeed(feed_storage.get(key, key)[1])

    def parse_feed(self, feed, url):
        if not feed:
            log.warning('parse_feed got %r for %s' % (feed, url))
            return {}
        if not isinstance(feed, basestring):
            feed = str(feed)
        feed = feedparser.parse(StringIO(feed))
        assert feed
        if feed.get('bozo_exception'):
            bozo_exc = str(feed['bozo_exception'])
            log.warning("Feed %s getting bozo_exception %r" % (feed, bozo_exc))
            feed['bozo_exception'] = bozo_exc
        return feed

    def store_feed(self, feed, addr):
        feed_storage[addr] = (time.time(), feed)
        return feed

    def get_feed(self, addr):
        return feed_storage[addr][1]

    def process_feed(self, parsed_feed, addr, olddata):
        """ Process the parsed feed.

        If `olddata` is provided, this method will look for new feed entries,
        and send notifications to the `feed.$FEED_URL` MokshaHub Topic.

        :param parsed_feed: A parsed :mod:`feedcache` feed
        :param addr: The URL of the feed
        :param olddata: The cached feed data
        """
        if not parsed_feed:
            log.error("Cannot process %r feed for %s" % (parsed_feed, addr))
            return

        chan = parsed_feed.get('channel', None)
        if chan:
            log.debug(chan.get('title', ''))

        # Previous data provided; look for new entries.
        if olddata:
            oldtitles = [entry.get('title') for entry in olddata['entries']]
            new_entries = parsed_feed.get('entries', [{}])
            if not len(new_entries):
                log.warning('Feed contains empty entries: %s' % addr)
                return

            # If there are no new entries, move on...
            newtitle = new_entries[0].get('title', None)
            if newtitle == oldtitles[0]:
                return

            # Send notifications for each new entry
            for entry in new_entries[::-1]:
                entry_title = entry.get('title', '[No Title]')
                channel_link = entry.get('channel', {'link': addr})['link']
                if entry['title'] not in oldtitles:
                    log.info('New feed entry found: %' % entry['title'])
                    self.hub.send_message('feed.%s' % channel_link,
                            {'title': entry_title, 'link': entry.get('link')})

    def get_page(self, data, args):
        return conditional_get_page(args, timeout=self.timeout)

    def start(self, data=None):
        d = defer.succeed(True)
        for feed in data:
            olddata = None
            if self.is_cached(feed):
                d.addCallback(self.get_feed_from_cache, feed)
                d.addErrback(self.on_error, (feed, 'fetching from cache'))
            else:
                d.addCallback(self.get_page, feed)
                d.addErrback(self.on_error, (feed, 'fetching'))
                d.addCallback(self.parse_feed, feed)
                d.addErrback(self.on_error, (feed, 'parsing'))
                olddata = self.get_feed(feed)
                d.addCallback(self.store_feed, feed)
                d.addErrback(self.on_error, (feed, 'storing'))
            d.addCallback(self.process_feed, feed, olddata)
            d.addErrback(self.on_error, (feed, 'processing'))
            del(olddata)
        return d


class FeederFactory(protocol.ClientFactory):

    protocol = FeederProtocol()

    def __init__(self, deferred_groups=60):
        """Initialize the Feeder Factory.

        :param deferred_groups: The number of simultaneous connections
        """
        self.protocol.factory = self
        self.deferred_groups = deferred_groups

    def start(self, addresses):
        """Divide into groups all the feeds to download.

        :param addresses: A list of feed urls
        """
        log.info("Starting the FeederFactory...")
        if len(addresses) > self.deferred_groups:
            url_groups = [[] for x in xrange(self.deferred_groups)]
            for i, addr in enumerate(addresses):
                url_groups[i % self.deferred_groups].append(addr)
        else:
            url_groups = [[addr] for addr in addresses]
        log.info("Creating %d url groups" % len(url_groups))
        for group in url_groups:
            self.protocol.start(group)


class FeedStream(PollingDataStream):
    """
    If you expose your feed widget on the moksha.widget entry point,
    then Moksha will automatically handle polling it.  Upon new entries,
    AMQP messages will be sent to the `feeds.$URL` queue.
    """
    frequency = timedelta(minutes=10)

    def poll(self):
        """ Poll all feeds in our feed cache """
        log.debug('FeedStream.poll()')

        feeds = set()
        for feed in feed_storage.keys():
            feeds.add(str(feed))

        # Read in all feeds from the `feeds.txt` file for testing...
        if os.path.isfile('feeds.txt'):
            feed_list = file('feeds.txt')
            for feed in feed_list.readlines():
                feeds.add(str(feed.strip()))

        f = FeederFactory()
        f.start(addresses=feeds)

    def stop(self):
        feed_storage.close()
        super(FeedStream, self).stop()
