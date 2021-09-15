import sys
from urllib.parse import urlparse
import json
import argparse
import logging
import datetime
from kafka import KafkaConsumer
from kevals.solr import SolrKevalsDB

# Set up a logging handler:
handler = logging.StreamHandler()
# handler = logging.StreamHandler(sys.stdout) # To use stdout rather than the default stderr
formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(filename)s.%(funcName)s: %(message)s")
handler.setFormatter(formatter)

# Attach to root logger
logging.root.addHandler(handler)

# Set default logging output for all modules.
logging.root.setLevel(logging.WARNING)

# Set logging for this module and keep the reference handy:
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def show_raw_stream(consumer, max_messages=None):
    msg_count = 0
    for message in consumer:
        msg_count += 1
        if max_messages and msg_count > max_messages:
            break
        # message value and key are raw bytes -- decode if necessary!
        # e.g., for unicode: `message.value.decode('utf-8')`
        print ("%s:%d:%d: key=%s value=%s" % (message.topic, message.partition,
                                              message.offset, message.key,
                                              message.value))


def show_stream(consumer, max_messages=None):
    msg_count = 0
    for message in consumer:
        msg_count += 1
        if max_messages and msg_count > max_messages:
            break
        # message value and key are raw bytes -- decode if necessary!
        # e.g., for unicode: `message.value.decode('utf-8')`
        j = json.loads(message.value.decode('utf-8'))
        if 'parentUrl' in j:
            # This is a discovered URL stream:
            #print("%010d:%04d: %-80s %s via %-80s" % (message.offset, message.partition,
            #                                    j['url'][-80:], j.get('hop','-'), j['parentUrl'][-80:]))
            print("%s %s via %-80s" % (j['url'], j.get('hop', '-'), j['parentUrl']))
        elif 'status_code' in j:
            # This is a crawled-event stream:
            #print("%s %-80s %s %s via %-80s" % (j['timestamp'], j['url'][-80:], j.get('status_code'), j.get('hop_path','-'), j.get('via', 'NONE')[-80:]))
            print("%s %s %s via %-80s" % (j['url'], j.get('status_code'), j.get('hop_path','-'), j.get('via', 'NONE')))
        else:
            # This is unknown!
            logger.error("Unrecognised stream! %s" % message.value)
            print ("%s:%d:%d: key=%s value=%s" % (message.topic, message.partition,
                                                  message.offset, message.key,
                                                  message.value))
            return


def summarise_stream(consumer, max_messages=None):
    tot = {}
    msg_count = 0
    for message in consumer:
        msg_count += 1
        if max_messages and msg_count > max_messages:
            break
        j = json.loads(message.value.decode('utf-8'))
        if msg_count%10000 == 0:
            print ("%s:%d:%d: key=%s value=%s" % (message.topic, message.partition,
                                                  message.offset, message.key,
                                                  message.value))
        # Select fields:
        if 'parentUrl' in j:
            url = j['url']
            via = j['parentUrl']
        elif 'status_code' in j:
            url = j['url']
            via = j.get('via',"-")
        else:
            # Skip unrecognised lines
            continue

        # Skip screenshot: etc. URLs for now:
        if not url.startswith("http"):
            continue

        # analyse:
        urlp = urlparse(url)
        viap = urlparse(via)
        stats = tot.get(urlp.hostname,{})
        if viap.hostname != urlp.hostname and not 'via' in stats:
            stats['via'] = via
            #print(j) # hop, isSeed, parentUrlMetadata.pathFromSeed
        stats['tot'] = stats.get('tot', 0) + 1
        tot[urlp.hostname] = stats

    print("URL Host\tDiscovered Via URL\tTotal URLs")
    for host in tot:
        print("%s\t%s\t%i" %(host, tot[host].get('via', '-'), tot[host]['tot']))

def to_solr_kevals(consumer, max_messages=None):

    skvdb = SolrKevalsDB('http://localhost:8913/solr/crawl_log_fc')
 
    def gen(consumer):
        for message in consumer:
            j = json.loads(message.value.decode('utf-8'))
            # Rename timestamp field
            j['log_timestamp'] = j.pop('timestamp')
            # Build ID based on timestamp and URL:
            j['id'] = 'crawl-log:%s/%s' % ( j['log_timestamp'], j['url'] )
            # Rename seed to source:
            if 'seed' in j:
                j['source'] = j.pop('seed')
            # Annotations, turing into fields as needed:
            annots = j.pop('annotations', '').split(',')
            new_annots = []
            for annot in annots:
                if annot.startswith('ip:'):
                    j['ip'] = annot[3:]
                elif annot.startswith('launchTimestamp:'):
                    # Map to launch_timestamp
                    ltss = annot[16:]
                    lts = datetime.datetime.strptime(ltss, '%Y%m%d%H%M%S%f')
                    j['launch_timestamp'] = "%sZ" % lts.isoformat()
                elif annot.startswith('dol:'):
                    j['dol'] = annot[4:]
                elif annot == "":
                    pass
                else:
                    # Replace any spaces with _ so the annotations are not split by Solr:
                    annot = annot.replace(' ', '_')
                    new_annots.append(annot)
            if len(new_annots) > 0:
                j['annotations'] = " ".join(new_annots)
            # start_time_plus_duration to start_time and duration:
            if 'start_time_plus_duration' in j:
                if j['start_time_plus_duration'] and '+' in j['start_time_plus_duration']:
                    jst, j['duration'] = j['start_time_plus_duration'].split('+')
                    jst = datetime.datetime.strptime(jst, '%Y%m%d%H%M%S%f')
                    j['start_time'] = "%sZ" % jst.isoformat()
                else:
                    j.pop('start_time_plus_duration')

            # Drop extra_info for now:
            j.pop('extra_info', None)

            # Add the crawler if not set:
            if 'crawler' not in j:
                if 'thread' in j:
                    j['crawler'] = 'Heritrix'
                else:
                    j['crawler'] = 'WebRender'

            # Pass to indexer:
            yield j
    
    skvdb.import_items_from(gen(consumer))
    


def main(argv=None):
    parser = argparse.ArgumentParser('(Re)Launch URIs into crawl queues.')
    parser.add_argument('-k', '--kafka-bootstrap-server', dest='bootstrap_server', type=str, default="localhost:9092",
                        help="Kafka bootstrap server(s) to use [default: %(default)s]")
    parser.add_argument("-L", "--latest", dest="latest", action="store_true", default=False, required=False,
                        help="Start with the latest messages rather than from the earliest. [default: %(default)s]")
    parser.add_argument("-S", "--summarise", dest="summarise", action="store_true", default=False, required=False,
                        help="Summarise the queue contents rather then enumerating it. [default: %(default)s]")
    parser.add_argument("-M", "--max-messages", dest="max_messages", default=None, required=False, type=int,
                        help="Maximum number of messages to process. [default: %(default)s]")
    parser.add_argument("-t", "--timeout", dest="timeout", default=10, required=False, type=int,
                        help="Seconds to wait for more messages before timing-out. '-1' for 'never'. [default: %(default)s]")
    parser.add_argument("-r", "--raw", dest="raw", action="store_true", default=False, required=False,
                        help="Show raw queue contents rather than re-formatting. [default: %(default)s]")
    parser.add_argument("-q", "--queue", dest="queue", default="uris.crawled.fc", required=False,
                        help="Name of queue to inspect. [default: %(default)s]")
    parser.add_argument("-G", "--group_id", dest="group_id", default=None, required=False,
                        help="Group ID to use. Setting this enables offset tracking. [default: %(default)s]")
    parser.add_argument("-C", "--client_id", dest="client_id", default="CrawlStreamsReport", required=False,
                        help="Client ID to use. [default: %(default)s]")

    # Parse the args:
    args = parser.parse_args()

    # Set up parameters based on args:
    if args.latest:
        starting_at = 'latest'
    else:
        starting_at = 'earliest'

    if args.timeout != -1:
        # Convert to ms:
        args.timeout = 1000*args.timeout

    # To consume messages and auto-commit offsets
    consumer = KafkaConsumer(args.queue, auto_offset_reset=starting_at,
                             bootstrap_servers=args.bootstrap_server,
                             consumer_timeout_ms=args.timeout,
                             max_partition_fetch_bytes=128*1024,
                             enable_auto_commit=False,
                             group_id=args.group_id,
                             client_id=args.client_id)

    # Choose what kind of analysis:
    if args.summarise:
        summarise_stream(consumer, max_messages=args.max_messages)
    elif args.raw:
        show_raw_stream(consumer, max_messages=args.max_messages)
    else:
        to_solr_kevals(consumer, max_messages=args.max_messages)
        #show_stream(consumer, max_messages=args.max_messages)


if __name__ == "__main__":
    sys.exit(main())
