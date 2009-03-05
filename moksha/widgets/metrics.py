## -*- coding: utf-8 -*-
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

"""
:mod:`moksha.widgets.metrics` -- Moksha Metrics
===============================================

This module contains Moksha-specific widgets and
DataStreams that provide live statistics of
Moksha's memory and CPU usage.

.. moduleauthor:: Luke Macken <lmacken@redhat.com>
"""

import subprocess
import logging
import os

from uuid import uuid4
from orbited import json
from tw.api import Widget, JSLink
from tw.jquery.ui import ui_progressbar_js
from tw.jquery.flot import flot_js, excanvas_js, flot_css

from moksha.api.hub import Consumer
from moksha.api.widgets.flot import LiveFlotWidget
from moksha.api.widgets.buttons import buttons_css
from moksha.api.streams import PollingDataStream
from moksha.lib.helpers import defaultdict
from moksha.widgets.jquery_ui_theme import ui_base_css

log = logging.getLogger(__name__)

class MokshaMemoryUsageWidget(LiveFlotWidget):
    name = 'Moksha Memory Usage'
    topic = 'moksha_mem_metrics'


class MokshaCPUUsageWidget(LiveFlotWidget):
    name = 'Moksha CPU Usage'
    topic = 'moksha_cpu_metrics'


class MokshaMessageMetricsConsumer(Consumer):
    """
    This consumer listens to all messages on the `moksha_message_metrics`
    topic, and relays the messgae to the message['headers']['topic']
    """
    topic = 'moksha_message_metrics'
    def consume(self, message):
        topic = message['headers'].get('topic')
        if topic:
            self.send_message(topic, json.encode(message['body']))
        else:
            log.error('No `topic` specified in moksha_message_metrics message')


class MokshaMessageMetricsWidget(LiveFlotWidget):
    """ A Moksha Message Benchmarking Widget.

    This widget will fire off a bunch of messages to a unique message topic.
    The MokshaMessageMetricsConsumer, being run in the Moksha Hub, will
    echo these messages back to the sender.  This widget will then graph
    the round-trip results.

    TODO:
    - display the latency
    """
    name = 'Moksha Message Metrics'
    template = """
        Messages sent: <span id="metrics_msg_sent">0</span><br/>
        <div id="metrics_sent_progress"></div>
        Messages received: <span id="metrics_msg_recv">0</span><br/>
        <div id="metrics_recv_progress"></div>
        <br/>
        <script>
            var NUM_MESSAGES = 100;
            var accum = 0.0;
            var flot_data = [];
            var x = 0;
            var start_time = 0;

            $('#metrics_sent_progress').progressbar({value: 0});
            $('#metrics_recv_progress').progressbar({value: 0});

            function run_message_metrics() {
                $('#metrics_sent_progress').progressbar('option', 'value', 0);
                $('#metrics_recv_progress').progressbar('option', 'value', 0);
                $('#metrics_msg_sent').text("0");
                $('#metrics_msg_recv').text("0");

                flot_data = [];
                x = 0;
                accum = 0.0;

                for (var i = 0; i < NUM_MESSAGES; i++) {
                    var start = new Date();
                    start_time = start.getTime();
                    stomp.send(start.getTime() + '', 'moksha_message_metrics',
                               {topic: '${topic}'});
                    $('#metrics_sent_progress').progressbar('option', 'value', i+1)
                    $('#metrics_msg_sent').text(i + 1 + '');
                }
                stomp.send('done', 'moksha_message_metrics',
                           {topic: '${topic}'});
            }

        </script>
        <div id="metrics_flot" style="width:390px;height:250px;" />
        <div id="metrics_avg"></div>
        <div id="metrics_msg_sec"></div>
        <br/>
        <center>
          <a href="#" class="opaquebutton" onclick="run_message_metrics(); return false"><span>Send 100 Messages</span></a>
        </center>
    """
    onmessage = """
        if (json == 'done') {
            avg = accum / (NUM_MESSAGES * 1.0);
            $('#metrics_recv_progress').progressbar('option', 'value', x+1)
            $.plot($('#metrics_flot'), [{data: flot_data, lines: {show: true}}]);
            $('#metrics_avg').text('Average round trip: ' + avg + ' seconds.');
            var start = new Date();
            seconds = (start.getTime() - start_time) / 1000.0;
            $('#metrics_msg_sec').text('Messages per second: ' + NUM_MESSAGES / seconds);
        } else {
            var now = new Date();
            seconds = (now.getTime() - json) / 1000.0;
            accum = accum + seconds;
            flot_data.push([x, seconds]);
            $('#metrics_recv_progress').progressbar('option', 'value', x+1)
            $('#metrics_msg_recv').text(x + 1 + '');
            x = x + 1;
        }
    """
    javascript = [
            excanvas_js, flot_js,
            # Provide our own jQuery ui until tw.jquery gets 1.6
            JSLink(link='/javascript/jquery-ui-personalized-1.6rc6.min.js',
                   modname=__name__)
            ]
    css = [ui_base_css, flot_css, buttons_css]

    def update_params(self, d):
        d.topic = str(uuid4())
        super(MokshaMessageMetricsWidget, self).update_params(d)


class MokshaMetricsDataStream(PollingDataStream):
    frequency = 2
    procs = ('orbited', 'paster', 'moksha')
    cpu_usage = defaultdict(list)
    programs = None

    def __init__(self):
        self.programs = []
        for program in self.mem():
            for proc in self.procs:
                if program[-1].startswith(proc) or proc in program[-1]:
                    self.programs.append(program)

        super(MokshaMetricsDataStream, self).__init__()

    def poll(self):
        i = 0
        pids = {}
        mem_data = {
            'data': [],
            'options': {
                'xaxis': {'ticks': []},
                'legend': {'position': 'nw', 'backgroundColor': 'null'}
            }
        }
        cpu_data = {
            'data': [],
            'options': {
                'xaxis': {'min': 0, 'max': 50},
                'yaxis': {'min': 0, 'max': 100},
                'legend': {
                    'position': 'nw',
                    'backgroundColor': 'null'
                }
            }
        }

        for program in self.programs:
            for proc in self.procs:
                if program[-1].startswith(proc) or proc in program[-1]:
                    pids[program[0]] = program[-1]
                    y = float(program[-2].split()[0])
                    mem_data['data'].append({
                            'data': [[i, y]],
                            'bars': {'show': 'true'},
                            'label': program[-1],
                            })
                    mem_data['options']['xaxis']['ticks'].append(
                            [i + 0.5, program[-1]])
                    i += 1

        self.send_message('moksha_mem_metrics', [mem_data])

        cmd = ['/usr/bin/top', '-b', '-n 1']
        for pid in pids:
            cmd += ['-p %s' % pid]

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        out, err = p.communicate()
        out = out.strip().split('\n')
        for i, line in enumerate(out):
            if line.lstrip().startswith('PID'):
                out = out[i+1:]
                break

        for line in out:
            splitline = line.split()
            pid = splitline[0]
            cpu_usage = float(splitline[-4])
            for history in self.cpu_usage[pid]:
                history[0] -= 1
            self.cpu_usage[pid].append([50, cpu_usage])
            self.cpu_usage[pid] = self.cpu_usage[pid][-51:]

        for pid, history in self.cpu_usage.items():
            cpu_data['data'].append({
                'data': history,
                'lines': {'show': 'true', 'fill': 'true'},
                #'points': {'show': 'true'},
                'label': pids[pid],
                })

        self.send_message('moksha_cpu_metrics', [cpu_data])

    def mem(self):
        """
        Returns a list of per-program memory usage.

             Private  +  Shared   =  RAM used     Program

           [["39.4 MiB", "10.3 MiB", "49.8 MiB",  "Xorg"],
            ["42.2 MiB", "12.4 MiB", "54.6 MiB",  "nautilus"],
            ["52.3 MiB", "10.8 MiB", "63.0 MiB",  "liferea-bin"]
            ["171.6 MiB", "11.9 MiB", "183.5 MiB", "firefox-bin"]]

        Taken from the ps_mem.py script written by Pádraig Brady.
        http://www.pixelbeat.org/scripts/ps_mem.py
        """
        our_pid=os.getpid()
        results = []
        global have_pss
        have_pss=0

        def kernel_ver():
            """ (major,minor,release) """
            kv=open("/proc/sys/kernel/osrelease").readline().split(".")[:3]
            for char in "-_":
                kv[2]=kv[2].split(char)[0]
            return (int(kv[0]), int(kv[1]), int(kv[2]))

        kv=kernel_ver()

        def getMemStats(pid):
            """ return Private,Shared """
            global have_pss
            Private_lines=[]
            Shared_lines=[]
            Pss_lines=[]
            pagesize=os.sysconf("SC_PAGE_SIZE")/1024 #KiB
            Rss=int(open("/proc/"+str(pid)+"/statm").readline().split()[1])*pagesize
            if os.path.exists("/proc/"+str(pid)+"/smaps"): #stat
                for line in open("/proc/"+str(pid)+"/smaps").readlines(): #open
                    if line.startswith("Shared"):
                        Shared_lines.append(line)
                    elif line.startswith("Private"):
                        Private_lines.append(line)
                    elif line.startswith("Pss"):
                        have_pss=1
                        Pss_lines.append(line)
                Shared=sum([int(line.split()[1]) for line in Shared_lines])
                Private=sum([int(line.split()[1]) for line in Private_lines])
                #Note Shared + Private = Rss above
                #The Rss in smaps includes video card mem etc.
                if have_pss:
                    pss_adjust=0.5 #add 0.5KiB as this average error due to trunctation
                    Pss=sum([float(line.split()[1])+pss_adjust for line in Pss_lines])
                    Shared = Pss - Private
            elif (2,6,1) <= kv <= (2,6,9):
                Shared=0 #lots of overestimation, but what can we do?
                Private = Rss
            else:
                Shared=int(open("/proc/"+str(pid)+"/statm").readline().split()[2])*pagesize
                Private = Rss - Shared
            return (Private, Shared)

        def getCmdName(pid):
            cmd = file("/proc/%d/status" % pid).readline()[6:-1]
            exe = os.path.basename(os.path.realpath("/proc/%d/exe" % pid))
            if exe.startswith(cmd):
                cmd=exe #show non truncated version
                #Note because we show the non truncated name
                #one can have separated programs as follows:
                #584.0 KiB +   1.0 MiB =   1.6 MiB    mozilla-thunder (exe -> bash)
                # 56.0 MiB +  22.2 MiB =  78.2 MiB    mozilla-thunderbird-bin
            return cmd

        cmds={}
        shareds={}
        count={}
        pids = {}

        for pid in os.listdir("/proc/"):
            try:
                pid = int(pid) #note Thread IDs not listed in /proc/ which is good
                #if pid == our_pid: continue
            except:
                continue
            try:
                cmd = getCmdName(pid)
            except Exception, e:
                #permission denied or
                #kernel threads don't have exe links or
                #process gone
                continue
            try:
                private, shared = getMemStats(pid)
            except:
                continue #process gone
            if shareds.get(cmd):
                if have_pss: #add shared portion of PSS together
                    shareds[cmd]+=shared
                elif shareds[cmd] < shared: #just take largest shared val
                    shareds[cmd]=shared
            else:
                shareds[cmd]=shared
            cmds[cmd]=cmds.setdefault(cmd,0)+private
            if count.has_key(cmd):
               count[cmd] += 1
            else:
               count[cmd] = 1
            pids[cmd] = pid

        #Add shared mem for each program
        total=0
        for cmd in cmds.keys():
            cmds[cmd]=cmds[cmd]+shareds[cmd]
            total+=cmds[cmd] #valid if PSS available

        sort_list = cmds.items()
        sort_list.sort(lambda x,y:cmp(x[1],y[1]))
        sort_list=filter(lambda x:x[1],sort_list) #get rid of zero sized processes

        #The following matches "du -h" output
        def human(num, power="Ki"):
            powers=["Ki","Mi","Gi","Ti"]
            while num >= 1000: #4 digits
                num /= 1024.0
                power=powers[powers.index(power)+1]
            return "%.1f %s" % (num,power)

        def cmd_with_count(cmd, count):
            if count>1:
               return "%s (%u)" % (cmd, count)
            else:
               return cmd

        for cmd in sort_list:
            results.append([
                "%d" % pids[cmd[0]],
                "%sB" % human(cmd[1]-shareds[cmd[0]]),
                "%sB" % human(shareds[cmd[0]]),
                "%sB" % human(cmd[1]),
                "%s" % cmd_with_count(cmd[0], count[cmd[0]])
            ])
        if have_pss:
            results.append(["", "", "", "%sB" % human(total)])

        return results


# @@ FIXME: We need to not insert two stomp widgets in this case...
#class MokshaMetricsWidget(Widget):
#    children = [MokshaCPUUsageWidget('moksha_cpu'),
#                MokshaMemoryUsageWidget('moksha_mem')]
#    template = """
#        <center>${c.moksha_cpu.label}</center>
#        ${c.moksha_cpu()}
#        <br/>
#        <center>${c.moksha_mem.label}</center>
#        ${c.moksha_mem()}
#    """
#    engine_name = 'mako'
