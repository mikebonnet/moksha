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

import os
import sys
import moksha
import logging
import pkg_resources

from webob import Request, Response
from shove import Shove
from pylons import config
from paste.deploy import appconfig
from feedcache.cache import Cache

from moksha.exc import ApplicationNotFound
from moksha.wsgiapp import MokshaApp

log = logging.getLogger(__name__)

class MokshaMiddleware(object):
    """
    A layer of WSGI middleware that is responsible for setting up the moksha
    environment, as well as handling every request/response in the application.

    If a request for an application comes in (/apps/$NAME), it will dispatch to
    the RootController of that application as defined in it's egg-info.

    """
    def __init__(self, application):
        log.info('Creating MokshaMiddleware')
        self.apps = {}
        self.widgets = {}
        self.mokshaapp = MokshaApp()
        self.application = application

        self.load_applications()
        self.load_widgets()
        self.load_renderers()
        self.load_configs()

        self.feed_storage = Shove('file://' + config['feed_cache'])
        self.feed_cache = Cache(self.feed_storage)

    def __call__(self, environ, start_response):
        environ['paste.registry'].register(moksha.apps, self.apps)
        environ['paste.registry'].register(moksha.widgets, self.widgets)
        environ['paste.registry'].register(moksha.feed_cache, self.feed_cache)
        request = Request(environ)
        if request.path.startswith('/appz'):
            app = request.path.split('/')[1]
            environ['moksha.apps'] = self.apps
            try:
                response = request.get_response(self.mokshaapp)
            except ApplicationNotFound:
                response = Response(status='404 Not Found')
        else:
            response = request.get_response(self.application)
        return response(environ, start_response)

    def load_applications(self):
        log.info('Loading moksha applications')
        for app_entry in pkg_resources.iter_entry_points('moksha.application'):
            if not app_entry.name in self.apps:
                log.info('Loading %s application' % app_entry.name)
                app_class = app_entry.load()
                app_path = app_entry.dist.location
                self.apps[app_entry.name] = {
                        'name': app_entry.name,
                        'controller': app_class(),
                        'path': app_path,
                        }

    def load_widgets(self):
        log.info('Loading moksha widgets')
        for widget_entry in pkg_resources.iter_entry_points('moksha.widget'):
            if not widget_entry.name in self.widgets:
                log.info('Loading %s widget' % widget_entry.name)
                widget_class = widget_entry.load()
                widget_path = widget_entry.dist.location
                self.widgets[widget_entry.name] = {
                        'name': widget_entry.name,
                        'widget': widget_class(),
                        'path': widget_path,
                        }

    def load_renderers(self):
        """ Load our template renderers with our application paths """
        template_paths = config['pylons.paths']['templates']
        for app in self.apps.values():
            if app['path'] not in template_paths:
                template_paths.append(app['path'])

        from mako.lookup import TemplateLookup
        config['pylons.app_globals'].mako_lookup = TemplateLookup(
            directories=template_paths, module_directory=template_paths,
            input_encoding='utf-8', output_encoding='utf-8',
            imports=['from webhelpers.html import escape'],
            default_filters=['escape'], filesystem_checks=False)

        from genshi.template import TemplateLoader
        def template_loaded(template):
            "Plug-in our i18n function to Genshi."
            template.filters.insert(0, Translator(ugettext))
        config['pylons.app_globals'].genshi_loader = TemplateLoader(
            search_path=template_paths, auto_reload=False,
            callback=template_loaded)

    def load_configs(self):
        """ Load the configuration files for all applications.

        Here we iterate over all applications, loading their configuration
        files and merging their [DEFAULT] configuration into ours.  This
        requires that applications do not have conflicting configuration
        variable names.  To mitigate this, applications should use some basic
        variable namespacing, such as `myapp.myvariable = myvalue`.

        We first make sure to load up Moksha's configuration, for the cases
        where it is being run as WSGI middleware in a different environment.

        """
        moksha_conf = os.path.abspath(__file__ + '/../../../')
        for app in [{'path': moksha_conf}] + self.apps.values():
            for configfile in ('production.ini', 'development.ini'):
                confpath = os.path.join(app['path'], configfile)
                if os.path.exists(confpath):
                    log.debug('Loading configuration: %s' % confpath)
                    conf = appconfig('config:' + confpath)
                    for entry in conf.global_conf:
                        if entry.startswith('_'):
                            continue
                        if entry in config:
                            log.warning('Conflicting variable: %s' % entry)
                            continue
                        else:
                            config[entry] = conf.global_conf[entry]
                            log.debug('Set `%s` in global config' % entry)
                    break