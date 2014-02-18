#!/usr/bin/env python

"""Daemon which watches a configured queue for messages and for each, calls a
webservice, storing the result in a WARC file."""

import os
import sys
import gzip
import pika
import uuid
import shutil
import logging
import requests
import settings
from daemonize import Daemon
from datetime import datetime
from hanzo.warctools import WarcRecord
from warcwriterpool import WarcWriterPool
from hanzo.warctools.warc import warc_datetime_str

logger = logging.getLogger( "harchiverd" )
handler = logging.FileHandler( settings.LOG_FILE )
formatter = logging.Formatter( "[%(asctime)s] %(levelname)s: %(message)s" )
handler.setFormatter( formatter )
logger.addHandler( handler )
logger.setLevel( logging.DEBUG )

#Try to set logging output for all modules.
logging.root.setLevel( logging.DEBUG )
logging.getLogger( "" ).addHandler( handler )

def write_outlinks( har, dir ):
	"""Writes outlinks in the HAR to a gzipped file."""
	filename = "%s/%s.schedule.gz" % ( dir, str( datetime.now().strftime( "%s" ) ) )
	with gzip.open( filename, "wb" ) as o:
		for entry in har[ "log" ][ "entries" ]:
			o.write( "F+ %s E %s\n" % entry[ "request" ][ "url" ] )

def callback( warcwriter, body ):
	"""Passed a URL, passes that URL to a webservice, storing the
	return content in a WARC file. If given a directory, will
	write the outlinks to a file therein."""
	try:
		logger.debug( "Message received: %s." % body )
		dir = None
		if "|" in body:
			#Old-style messages
			url, dir = body.split( "|" )
		else:
			url = body
		ws = "%s/%s" % ( settings.WEBSERVICE, url )
		logger.debug( "Calling %s" % ws )
		r = requests.get( ws )
		if r.status_code == 200:
			har = r.content
			headers = [
				( WarcRecord.TYPE, WarcRecord.METADATA ),
				( WarcRecord.URL, url ),
				( WarcRecord.CONTENT_TYPE, "application/json" ),
				( WarcRecord.DATE, warc_datetime_str( datetime.now() ) ),
				( WarcRecord.ID, "<urn:uuid:%s>" % uuid.uuid1() ),
			]
			warcwriter.write_record( headers, "application/json", har )
			if dir is not None:
				logger.debug( "Writing outlinks to %s" % dir )
				write_outlinks( har, dir )
		else:
			logger.warning( "None-200 response for %s; %s" % ( body, r.content ) )
	except Exception as e:
		logger.error( "%s [%s]" % ( str( e ), body ) )

class HarchiverDaemon( Daemon ):
	"""Maintains a connection to the queue."""
	def run( self ):
		warcwriter = WarcWriterPool( gzip=True, output_dir=settings.OUTPUT_DIRECTORY )
		while True:
			try:
				logger.debug( "Starting connection %s:%s." % ( settings.HAR_QUEUE_HOST, settings.HAR_QUEUE_NAME ) )
				connection = pika.BlockingConnection( pika.ConnectionParameters( settings.HAR_QUEUE_HOST ) )
				channel = connection.channel()
				channel.queue_declare( queue=settings.HAR_QUEUE_NAME, durable=True )
				for method_frame, properties, body in channel.consume( settings.HAR_QUEUE_NAME ):
					callback( warcwriter, body )
					channel.basic_ack( method_frame.delivery_tag )
			except Exception as e:
				logger.error( str( e ) )
				requeued_messages = channel.cancel()
				logger.debug( "Requeued %i messages" % requeued_messages )
 
if __name__ == "__main__":
	"""Sets up the daemon."""
	if len( sys.argv ) == 2:
		daemon = HarchiverDaemon( settings.PID_FILE )
	elif len( sys.argv ) == 3:
		#Possibly pass a PID file to enable multiple instances.
		daemon = HarchiverDaemon( sys.argv[ 2 ] )
	logger.debug( "Arguments: %s" % sys.argv )
	if len( sys.argv ) >= 2:
		if "start" == sys.argv[ 1 ]:
			logger.info( "Starting harchiverd." )
			daemon.start()
		elif "stop" == sys.argv[ 1 ]:
			logger.info( "Stopping harchiverd." )
			daemon.stop()
		elif "restart" == sys.argv[ 1 ]:
			logger.info( "Restarting harchiverd." )
			daemon.restart()
		else:
			print "Unknown command"
			print "usage: %s start|stop|restart" % sys.argv[ 0 ]
			sys.exit( 2 )
		logger.debug( "Exiting." )
		sys.exit( 0 )
	else:
		print "usage: %s start|stop|restart" % sys.argv[ 0 ]
		sys.exit( 2 )
