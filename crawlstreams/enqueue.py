#!/usr/bin/env python
# encoding: utf-8
'''
agents.launch -- Feeds URIs into queues

@author:     Andrew Jackson

@copyright:  2016 The British Library.

@license:    Apache 2.0

@contact:    Andrew.Jackson@bl.uk
@deffield    updated: 2016-01-16
'''

import json
import logging
from datetime import datetime
import time
import mmh3
import binascii
import struct
from urllib.parse import urlparse
from kafka import KafkaProducer


# Set logging for this module and keep the reference handy:
logger = logging.getLogger(__name__)


class KafkaLauncher(object):
    '''
    classdocs
    '''

    def __init__(self, kafka_server, topic=None):
        '''
        Constructor
        '''
        self.producer = KafkaProducer(
            bootstrap_servers=kafka_server,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'))
        self.topic = topic

    def send_message(self, key, message, topic=None):
        """
        Sends a message to the given queue.
        """
        #
        if not topic:
            topic = self.topic

        logger.info("Sending key %s, message: %s" % (key, json.dumps(message)))
        self.producer.send(topic, key=key, value=message)

    def launch(self, uri, source, isSeed=False, forceFetch=False, sheets=[], hop="",
               recrawl_interval=None, reset_quotas=None, webrender_this=False, launch_ts=None, inherit_launch_ts=False,
               parallel_queues=1):

        # Set up a launch timestamp:
        if launch_ts:
            if isinstance(launch_ts, str):
                if launch_ts.lower() == "now":
                    launch_ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            elif isinstance(launch_ts, datetime):
                # Convert to UTC timestamp:
                launch_ts = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.mktime(launch_ts.timetuple())))
            else:
                raise Exception("Cannot handle launch_ts of this type! %s" % launch_ts)

        # Set up the launch message:
        curim = {}
        curim['headers'] = {}
        target_sheet = {}
        # curim['headers']['User-Agent'] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/37.0.2062.120 Chrome/37.0.2062.120 Safari/537.36"
        curim['method'] = "GET"
        curim['parentUrl'] = uri
        curim['parentUrlMetadata'] = {}
        curim['parentUrlMetadata']['pathFromSeed'] = ""
        curim['parentUrlMetadata']['heritableData'] = {}
        curim['parentUrlMetadata']['heritableData']['refreshDepth'] = 1
        curim['parentUrlMetadata']['heritableData']['source'] = source
        curim['parentUrlMetadata']['heritableData']['heritable'] = ['source', 'heritable', 'refreshDepth']
        curim['parentUrlMetadata']['heritableData']['annotations'] = []
        curim['isSeed'] = isSeed
        curim['forceFetch'] = forceFetch # Even seeds might need this, depending on uriUniqFilter configuration.
        curim['url'] = uri
        curim['hop'] = hop
        if len(sheets) > 0:
            curim['sheets'] = sheets
        if recrawl_interval:
            curim['recrawlInterval'] = recrawl_interval
        if webrender_this:
            curim['parentUrlMetadata']['heritableData']['annotations'].append('WebRenderThis')
        if reset_quotas:
            curim['parentUrlMetadata']['heritableData']['annotations'].append('resetQuotas')
        if launch_ts:
            if inherit_launch_ts:
                # Define a Heritrix crawl configuration sheet specific to this target:
                target_sheet['recentlySeen.launchTimestamp'] = launch_ts
            else:
                # Just specify it for this URL:
                curim['parentUrlMetadata']['heritableData']['launchTimestamp'] = launch_ts # New syntax
                curim['parentUrlMetadata']['heritableData']['launch_ts'] = launch_ts
                #curim['parentUrlMetadata']['heritableData']['heritable'].append('launch_ts')

        # Support launching URLs with parallel queues
        if parallel_queues > 1:
            target_sheet['queueAssignmentPolicy.parallelQueues'] = parallel_queues
            target_sheet['queueAssignmentPolicy.parallelQueuesRandomAssignment'] = True
            target_sheet['queueAssignmentPolicy.deferToPrevious'] = False

        # Patch in the target-level sheet if it's been used:
        if len(target_sheet) > 0:
            curim['targetSheet'] = target_sheet

        # Record the timestamp
        curim['timestamp'] = datetime.utcnow().isoformat()

        # Determine the key, hashing the 'authority' (should match Java version):
        key = binascii.hexlify(struct.pack("<I", mmh3.hash(urlparse(uri).netloc, signed=False)))

        # Push a 'seed' message onto the rendering queue:
        self.send_message(key, curim)

    def flush(self):
        self.producer.flush()

    def close(self):
        self.producer.close()