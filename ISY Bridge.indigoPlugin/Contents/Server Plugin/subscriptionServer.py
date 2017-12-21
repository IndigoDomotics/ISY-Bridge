#! /usr/bin/env python
# -*- coding: utf-8 -*-
#							ISY INSTEON Controller
#							subscriptionServer.py
#
# 2014/02/01 [JMM] Updated to an issue:
#		* The control event that gets caught for program runs returns the program
#		  number not left-padded with zeros to 4 places, but the command to execute
#		  a program does (love consistency). So, when we catch the message we do
#		  the padding and also prepend "[self.ISY.id]" to it before calling the
#		  method back on the main thread to process any events for that program
#		  execution.
#		* Fixed an error report that occurred erroneously when the plugin tells
#		  us to stop running.
################################################################################

import socket
import base64
from xml.dom.minidom import parseString
from threading import Timer

########################################################

kHeartbeatTimeout = 250  # ISY sends heartbeat every 120 seconds, 250 seconds allows one miss
kSocketTimeout = 30

########################################################
class HttpBaseForm(object):
	####################################################

	def __init__(self, conn):
		self.getHeaders(conn)
		self.getBody(conn, self.getContentLength())

	def getHeaders(self, conn):
		self.headers = ''
		while True:
			d=conn.recv(1)
			if not d:
				raise Exception('ISY closed subscription connection')
			self.headers = self.headers + d
			if d == '\n':
				l = len(self.headers)
				if l >= 4:
					if self.headers[l-2] == '\r' and self.headers[l-3] == '\n' and self.headers[l-4] == '\r':
						break

	def getContentLength(self):
		value = 0
		splitHeaders = self.headers.split('\r\n')
		for header in splitHeaders:
			if 'content-length' in header.lower():
				value = int(header[len('content-length')+1:])
				break
		return value

	def getBody(self, conn, contentLength):
		self.body = ''
		while len(self.body) < contentLength:
			d = conn.recv(1)
			if not d:
				raise Exception('ISY closed subscription connection')
			self.body = self.body + d

########################################################
class HttpResponse(HttpBaseForm):
	####################################################

	def __init__(self, conn):
		HttpBaseForm.__init__(self, conn)
		if self.headers != '':
			self.getStatus()
			self.getSid()
						
	def getStatus(self):
		if self.headers[:8] == 'HTTP/1.1':
			self.status = int(self.headers[8:self.headers.index('OK')])
		else:
			self.status = 0
			
	def getSid(self):
		if '<SID>' in self.body:
			self.sid = self.body[self.body.index('<SID>')+5:self.body.index('</SID>')]
		else:
			self.sid = None

########################################################
class HttpRequest(HttpBaseForm):
	####################################################

	def __init__(self, conn):
		HttpBaseForm.__init__(self, conn)
		if self.headers != '':
			self.getRequestType()
						
	def getRequestType(self):
		self.requestType = self.headers[:self.headers.index(' ')]

########################################################
class SubscriptionServer(object):
	####################################################
	
	def __init__(self, plugin, dev, deviceDict):
		self.plugin = plugin
		self.debugLog = plugin.debugLog
		self.errorLog = plugin.errorLog
		self.ISY = dev
		self.devices = deviceDict
		self.badDevices = {}
		self.conn = ''
		self.sid = None
		self.stop = False
		self.connectionIsValid = False
		self.heartbeatTimeout = ''
		
	def encodeBase64(self, arg):
		encoded = base64.b64encode(arg)
		self.debugLog(encoded)
		return encoded

	def subscribe(self):

		ISYIP = self.ISY.address
		authorization = self.encodeBase64(self.ISY.pluginProps['authorization'])
		
		soapBody = "<s:Envelope><s:Body><u:Subscribe xmlns:u='urn:udi-com:service:X_Insteon_Lighting_Service:1'><reportURL>REUSE_SOCKET</reportURL><duration>infinite</duration></u:Subscribe></s:Body></s:Envelope>\r\n"

		soapHeaders = ('POST /services HTTP/1.1\r\n'
				'Host: %s\r\n'
				'Authorization: Basic %s\r\n'
				'Content-Length: %d\r\n'
				"Content-Type: text/xml; charset='utf-8'\r\n\r\n") % (ISYIP, authorization, len(soapBody))

		self.conn.send('%s%s' % (soapHeaders, soapBody))

	def extractFromXML(self, xml, tag):
		elementList = xml.getElementsByTagName(tag)
		#element = elementList[0]
		#return element.nodeValue
		#return ''.join( [node.data for node in element.childNodes] )
		#self.debugLog('jms/in extractFromXML here is xml and elementList[0]')
		#self.debugLog(xml)
		#self.debugLog(elementList[0])
		#return elementList[0].toxml().replace('<' + tag + '>','').replace('</' + tag + '>','')
		element = elementList[0].toxml()
		#self.debugLog("element is: %s" % element)
		start = element.find('>') + 1
		end = element.rfind('<')
		#self.debugLog('element [start:end] is %s' % element[start:end])
		return element[start:end]
		
	def lostHeartbeat(self):
		self.errorLog('Failed to receive heartbeat - lost connection with ISY: %s' % self.ISY.address)
		self.connectionIsValid = False
		
	def startServer(self):
		self.stop == False
		while self.stop == False:
			try:
				self.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				self.conn.settimeout(kSocketTimeout)
				self.conn.connect((self.ISY.address, 80))
				self.connectionIsValid = True
				self.debugLog('connected to ISY: %s' % self.ISY.address)
			except Exception, e:
				self.errorLog('Failed to connect to ISY: %s Error: %s' % (self.ISY.address, e))
				self.plugin.sleep(5)
				continue	# failed to connect to ISY, skip rest of loop and try again

			self.subscribe()
			response = HttpResponse(self.conn)
			if response.sid == None:
				self.errorLog('Failed to establish ISY subscription: %s%s' % (response.headers, response.body))
				self.conn.close()
				self.plugin.sleep(5)
				continue	# ISY failed to respond appropriately to subscription, close connection and try again

			self.debugLog('SID: %s' % response.sid)
			self.sid = response.sid
			self.seqnum = 0
			self.ISY.updateStateOnServer('connectionStatus', 'connected')
			self.debugLog('subscribed to ISY: %s' % self.ISY.address)
				
 			self.heartbeatTimeout = Timer(kHeartbeatTimeout, self.lostHeartbeat)
 			self.heartbeatTimeout.start()

			while self.stop == False and self.connectionIsValid == True:
				try:
					request = HttpRequest(self.conn)
					if '<Event' not in request.body:
						self.errorLog('invalid request body: %s' % request.body)
						continue
					self.debugLog('<<--- startServer received ISY event %s' % request.body)
					event = parseString(request.body).getElementsByTagName('Event')[0]
					sid = event.getAttribute('sid')
					if sid != self.sid:
						self.errorLog('caught invalid sid: %s' % request.body)
						continue
					seqnum = int(event.getAttribute('seqnum'))
					if seqnum > self.seqnum + 1:
						if seqnum > self.seqnum + 2:
							self.errorLog('Missing Sequence Numbers: %d-%d' % (self.seqnum+1, seqnum-1))
						else:
							self.errorLog('Missing Sequence Number: %d' % seqnum-1)
					self.seqnum = seqnum
					control = self.extractFromXML(event, 'control')
					action = self.extractFromXML(event, 'action')
					node = self.extractFromXML(event, 'node')
					eventInfo = self.extractFromXML(event, 'eventInfo')
					self.debugLog('seqnum: %d control: %s action: %s node: %s eventInfo: %s' % (seqnum, control, action, node, eventInfo))
					self.handleEvent(control, action, node, eventInfo)
				except socket.timeout:
					self.debugLog('socket timeout')
				except Exception, e:
					# we don't want to report an error if we've been told to stop since
					# that's likely the cause of the error anyway
					if not self.stop:
						self.errorLog('%s: %s' % (e, self.ISY.address))
						self.connectionIsValid = False
 			self.heartbeatTimeout.cancel()
			self.conn.close()
			self.debugLog('closed connection to ISY')
			self.ISY.updateStateOnServer('connectionStatus', 'disconnected')
		
	def deleteDevice(self, address):
		if address in self.devices:
			del self.devices[address]

	def unSubscribe(self, conn):

		ISYIP = self.ISY.address
		authorization = self.encodeBase64(self.ISY.pluginProps['authorization'])
		
		soapBody = "<s:Envelope><s:Body><u:Unsubscribe xmlns:u='urn:udi-com:service:X_Insteon_Lighting_Service:1'><SID>%s</SID></u:Unsubscribe></s:Body></s:Envelope>\r\n" % self.sid

		soapHeaders = ('POST /services HTTP/1.1\r\n'
				'Host: %s\r\n'
				'Authorization: Basic %s\r\n'
				'Content-Length: %d\r\n'
				"Content-Type: text/xml; charset='utf-8'\r\n\r\n") % (ISYIP, authorization, len(soapBody))

		conn.send('%s%s' % (soapHeaders, soapBody))

	def stopServer(self):
		self.stop = True
		conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		conn.connect((self.ISY.address, 80))
		self.unSubscribe(conn)
		response = HttpResponse(conn)
		self.debugLog('Unsubscribe Response: %d' % response.status)
		conn.close()

# handleEvent:
# This is the main worker routine that is called when an ISY event appears.
# It peels off some common events (errors), then calls routines for all control
# events (those that begin with _) and then has its own convoluted logic for
# the device-specific events such as "ST" (status) that might show up.
#/jms/171220
	
	def handleEvent(self, control, action, node, eventInfo):
		self.debugLog('<<----called: handleEvent')
# The ERR seems to come from devices that are red in the ISY GUI
# but work perfectly well anyway.  So I'm going to ignore ERR
# and only accept _3 NE events.  May God have mercy on my soul./jms
#		if control == 'ERR' or (control == '_3' and action == 'NE'):  # PREVIOUS CODE
		if control == 'ERR': 
			self.debugLog('IGNORED communications error %s %s %s %s' % (node, control, action, eventInfo))

                elif (control == '_3' and action == 'NE'):
			self.debugLog('Communication Error: %s %s %s %s' % (node, control, action, eventInfo))
			# move the device to the bad list
 			if node in self.devices:
  				needsDeletion = self.plugin.communicationError(self.ISY, self.devices[node])
 				if needsDeletion != True:
					self.badDevices[node] = self.devices[node]
 				del self.devices[node]
	
		elif control[0] == '_':
			self.handleControlEvent(control, action, node, eventInfo)

		elif control in ['RR', 'OL']:    # ramp rate and on level not currently handled
			pass
		
		else:
			try:
				#self.debugLog('in handleEvent checkpoint 1')
				#self.debugLog('   node is %s' % (node))
				#self.debugLog('   dumping devices array')
				#self.debugLog(str(self.devices))
				#self.debugLog('   end of devices dump')
# A problem here is that devices may show up which are not
# in our devices array, either because we don't support them
# or perhaps because they were added later.  Thus, if the device
# is not in the devices dictionary, then simply ignore the event (debug log)/jms
                                if node in self.devices: 
					device = self.devices[node]
				else:
					self.debugLog('IGNORED event for device %s' % node)
					return
				if device.deviceTypeId in ['ISYRelay', 'ISYIrrigation', 'ISYIODevice']:
					self.handleRelayEvent(device, control, action)
					
				elif device.deviceTypeId == 'ISYDimmer':
					self.handleDimmerEvent(device, control, action)
					
				elif device.deviceTypeId == 'ISYThermostat':
					self.handleThermostatEvent(device, control, action)
					
				else:
					self.errorLog('unhandled feedback control: %s action: %s node: %s eventInfo: %s' % (control, action, node, eventInfo))
			except:
				nodeParts = node.split(' ')
				if nodeParts[3] != '1':    # not currently handling secondary nodes (i.e., non-load buttons on keypadlinc)
					pass
				elif node in self.badDevices:
					if control == 'ST':   # if we are receiving state info on a bad device, it's no longer bad
						self.debugLog('device resumed communication: %s' % node)
						self.devices[node] = self.badDevices[node]
						del self.badDevices[node]
						self.plugin.communicationResumed(self.ISY, self.devices[node])
						self.plugin.queryDevice(self.ISY, node)
				else:
					self.debugLog('No plugin device defined for node: %s control: %s action: %s eventInfo: %s' % (node, control, action, eventInfo))
					self.plugin.undefinedDeviceDetected(node)

# handleControlEvent:
# This is called by handleEvent for all control events, which are defined as "events with a control beginning with _"
# The code expects that the XML that comes over will ALWAYS have control, action, node, and eventInfo, although
# it is not clear from the documentation if that is necessarily true.  
#
#         <xsd:complexType name="Event">
#                <xsd:sequence>
#                        <xsd:annotation>
#                                <xsd:documentation>
#                                        An XML structure with specific information for each event type
#                                </xsd:documentation>
#                        </xsd:annotation>
#                        <xsd:element name="control" type="ue:EventTypes"/>
#                        <xsd:element name="action" type="ue:EventActionTypes"/>
#                        <xsd:element name="node" type="xsd:string"/>
#                        <xsd:element name="eventInfo" type="xsd:string"/>
#                </xsd:sequence>
#
# This is what the documentation says. 
# /jms/171220

	def handleControlEvent(self, control, action, node, eventInfo):
		self.debugLog('<<----called: handleControlEvent')

# Heart Beat: _0
# (in this case, action is the number of seconds)
		if control == '_0':
			# ISY heartbeat
			self.conn.send('beat')
 			self.heartbeatTimeout.cancel()
 			self.heartbeatTimeout = Timer(kHeartbeatTimeout, self.lostHeartbeat)
 			self.heartbeatTimeout.start()
			self.debugLog('Rubatosis ... thump.thump ... thump.thump')

# Trigger Events: 0 = event status; 1 = client should get status; 2 = key changed; 3 = info string; 4 = IR learn mode;
#		  5 = schedule status changed; 6 = variable status changed; 7 = variable intialized;
#		  8 = current program key
		elif control == '_1':
			if action == '0':
				programXML = parseString('<prg>%s</prg>' % eventInfo)
				# we only get back the program number, and it's not zero padded to 4 places, so we
				# need to pad it out (4 becomes 0004) and prepend the ISY device id
				id = "[%i]%04i" % (self.ISY.id, int(self.extractFromXML(programXML, 'id')))
				s = self.extractFromXML(programXML, 's')
				if s == '22':
					fork = 'then'
					status = 'started'
				elif s == '21':
					fork = 'then'
					status = 'finished'
				elif s == '33':
					fork = 'else'
					status = 'started'
				elif s == '31':
					fork = 'else'
					status = 'finished'
				else:
					fork = 'unknown'
					status = 'unknown'
				self.plugin.programFeedback(id, fork, status)
				
#			elif action == '1':  # get status, subscribers must refresh - usage unclear - tbd

#			elif action == '2':  # key changed - usage unclear - tbd
			
			elif action == '3':  # information event
				# event viewer
				self.plugin.pluginEventViewer(self.ISY, eventInfo)

			elif action == '4':
				# ir learn mode - ignored for now
				pass
				
#			elif action == '5':  # schedule changed status - usage unclear - tbd

			elif action == '6':
				# variable status changed - ignored for now
				pass
				
			elif action == '7':
				# variable initialized - ignored for now
				pass

			elif action == '8': 
				# key - ignored for now/jms
				self.debugLog('Ignoring ISY control event _1/_8 (key): eventInfo: %s' % (eventInfo))
				
			else:
				self.errorLog('UNKNOWN ISY control _1 event for node: %s action: %s eventInfo: %s' % (node, action, eventInfo))

# _2 = Driver specific events
# Not really described in the manual other than "driver specific events."  Print/ignore
# /jms/171220
		elif control == '_2':
			self.plugin.pluginEventViewer(self.ISY, 'IGNORED Driver specific _2 %s %s %s' % (node, action, eventInfo))

# _3 = Node Changed/Updated
# NN = node renamed; NR = node removed; ND = node added; MV = node moved into a scene; CL = link changed;
# RG = removed from group; EN = enabled; PC = parent changed; PI = power info changed; DI = Device ID changed;
# DP = device property changed; GN = group renamed; GR = group removed; GD = group added; FN = folder renamed;
# FR = folder removed; FD = folder added; NE = Node Error (Communications Errors); CE = Node Error Cleared; 
# SN = Discovering Nodes/Linking; SC = Discovery Complete; WR = network renamed; WH = Pending Device Operation;
# WD = programming device; RV = Node Revised (UPB)
# /jms

			
 		elif control == '_3':

			if action == 'CE':
				if node in self.badDevices:
					self.devices[node] = self.badDevices[node]
					del self.badDevices[node]
					self.plugin.communicationResumed(self.devices[node])
					self.plugin.queryDevice(self.ISY, node)

#			NE is dealt with above
# 			elif action == 'NE':
# 				Plugin.errorLog('Comm Error: %s %s %s' % (control, action, eventInfo))

			elif action == 'NR':
				if node in self.devices:
					self.plugin.deviceNeedsDeletion(self.devices[node])
					del self.devices[node]
				
			elif action == 'ND':
				device = self.plugin.deviceNeedsAdding(self.ISY, eventInfo)
				if device != None:
					self.devices[device.address] = device
					self.plugin.queryDevice(ISY, node)
				
			elif action == 'NN':
				if node in self.devices:
					index = eventInfo.index('<newName>')
					index2 = eventInfo.index('</newName>')
					newName = eventInfo[index+9:index2]
					device = self.devices[node]
					device.name = newName
					device.replaceOnServer()

			elif action in ['GN', 'GR' 'GD']:		# add, delete scenes - should deal w/ this
				pass

			elif action in ['MV', 'CL', 'RG']:
				# group restructuring - ignored for now
				pass
				
			elif action == 'EN':
				# enabled - ignored for now
				pass
				
			elif action in ['PC', 'PI', 'DI', 'DP', 'RV']:
				# misc node changes - ignored for now
				pass

			elif action in ['FN', 'FR', 'FD']:
				# folder changes = ignored for now
				pass
				
			elif action in ['SN', 'SC','WR', 'WH', 'WD']:
				# ISY action states - ignored for now
				pass
				
			else:			# actions other than the above fall through here to see which are relevant
				self.errorLog('unhandled ISY control event: %s node: %s action: %s eventInfo: %s' % (control, node, action, eventInfo))

# _4 = System Config Updated Event
# 1 = time cofig updated; 2 = NTP settings updated; 3 = notification settings updated;
# 4 = NTP server communications error; 5 = Batch mode changed 1/on 0/off;
# 6 = battery device write mode changed: 1/auto 0/manual
# jms/171220
                elif control == '_4':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED system config _4 %s %s %s' % (node, action, eventInfo))

# _5 = System Status Event
# 0 = not busy; 1 = busy; 2 = completely idle; 4 = safe mode
# Note that on "1" the system "might ignore commands."  We do get these,
# but I am not sure if we care.  These are computers, and they should be queueing
# events.  I guess if we see a lot of these then we should figure out if we
# need to put in logic to handle them.
#/jms/171220
                elif control == '_5':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED system status _5 %s %s %s' % (node, action, eventInfo))

# _6 = Internet Access Event
# 0 = disabled; 1 = enabled; 2 = failed
#/jms/171220
                elif control == '_6':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED internet access _6 %s %s %s' % (node, action, eventInfo))

# _7 = System Progress Event
# 1 = progress updated event; 2.x = device adder info/warn/error event
#/jms/171220
                elif control == '_7':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED system progress _7 %s %s %s' % (node, action, eventInfo))

# _8 = Security System Event
# 0 = disconnected; 1 = connected
# DA = disarmed; AW = armed away; AS = armed stay; ASI = armed stay instant; AN = armed night
# ANI = armed night instant; AV = armed vacation
# I'm not going to do any alarm propagation from Indigo<->UDI since I don't have any,
# but if you wanted to start the integration from UDI->Indigo, then this is where you'd probably
# do a lot of the work.
#/jms/171220
                elif control == '_8':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED security event _8 %s %s %s' % (node, action, eventInfo))
                        # security system event - ignored for now
                        pass

# _9 = System Alert Event
# 1 = electricity peak demand; 2 = electricity max utilization; 3 = gas max utilization; 4 = water max utilization
# "A programmable alert sent to clients to do as they wish: beep, change colors, do something else"
# These all seem to have something to do with energy/utility usage
# So I'm ignoring them.
#/jms/171220
                elif control == '_9':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED system alert _9 %s %s %s' % (node, action, eventInfo))

# _10 = OpenADR event
# Open Auto Demand/Reponse actions
# Eventinfo structure holds: bPrice = base price; cPrice = current price
# 1 = OpenADR connection; 2 = OpenADR status; 4 = Utilization Report (total/watts/voltage/current)
# 5 = Error connecting to Flex Your Power; 6 = Flex Your Power (FYP) status
# 8 = OpenADR 2.0 registration; 9 = OpenADR Report; 10 = OpenADR Opt (look in oadrobjs.xsd for more info)
#/jms/172220
                elif control == '_10':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED OpenADR event _10 %s %s %s' % (node, action, eventInfo))

# _11 = Climate Events
# There are a ton of these; all require some sort of plugin (weatherbug module on ISY)
# 1 = Temp; 2 = Temp High; 3 = Temp Low; 4 = Feels Like; 5 = Temp Average
# 6 = Humidity; 7 = Humidity Rate; 8 = Pressure; 9 = Pressure Rate; 10 = Dew Point
# 11 = Wind Speed; 12 = Avg Wind Speed; 13 = Wind Direction; 14 = Avg Wind Direction;
# 15 = Gust Speed; 16 = Gust Direction; 17 = Rain Today; 18 = Light; 19 = Light Rate
# 20 = Rain Rate; 21 = Rain Rate Max; 22 = Evapotranspiration; 23 = Irrig. Reqt
# 24 = Water Deficit Yesterday; 25 = Elevation; 26 = Coverage (see ClimateCoverage)
# 27 = Intensity (see ClimateIntensity); 28 = Weather Condition (see ClimateWeatherCondition)
# 29 = Cloud Condition (see ClimateCloudCondition) 30 = Avg Temp Tomorrow; 31 = High Temp Tomorrow
# 32 = Low Temp Tomorrow; 33 = Humidity Tomorrow; 34 = Wind Speed Tomorrow; 35 = Gust Speed Tomorrow;
# 36 = Rain Tomorrow; 37 = Snow tomorrow; 38 = Coverage Tomorrow; 39 = Intensity Tomorrow;
# 40 = Weather Condition Tomorrow; 41 = Cloud Condition Tomorrow; 42 = 24 hr Avg Temp Forecast
# 43 = 24 hr High Temp Forecast; 44 = 24 hr Low Temp Forecast; 45 = 24 hr Humidity Forecast;
# 46 = 24 hr Rain Forecast; 27 = 24 hr Snow Forecast; 48 = 24 hr Coverage forecast; 49 = 24 hr Intensity Forecast
# 50 = 24 hr Condition FOrecast; 51 = 24 hr Cloud Forecast; 100 = Last successfully polled and processed timestamp
#/jms/171220
                elif control == '_11':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED weather event _11 %s %s %s' % (node, action, eventInfo))

# _12 = AMI Meter Events
                elif control == '_12':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED AMI Meter _12 %s %s %s' % (node, action, eventInfo))

# _13 = Electricity Monitor Events
#  Eventinfo contains # of channels, report actions, and raw message from Brultech
                elif control == '_13':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Electricity Monitor _13 %s %s %s' % (node, action, eventInfo))

# _14 = UPB Linker Events
# 1 = status; 2 = pending stop find; 3 = pending cancel device add
# _15 = UPB Adder Events
# 1 = device status
# _16 = UPB Status Event
                elif control in ['_14', '_15', '_16']:
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED UPB Event %s %s %s %s' % (control, node, action, eventInfo))

# _17 = Gas Meter Event
                elif control == '_17':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Gas Meter _17 %s %s %s' % (node, action, eventInfo))

# _18 = Zigbee Event
                elif control == '_18':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Zigbee Action _18 %s %s %s' % (node, action, eventInfo))

# _19 = ELK Events (actions and event info defined in elkobjs.xld)
# These are ignored; if you want to do Elk, talk directly to the Indigo with the Elk
#/jms/171220
                elif control == '_19':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED ELK event _19 %s %s %s' % (node, action, eventInfo))

# _20 = Device Linker events (defined in DeviceLinkerEventInfo)
# Device Linker Events (1 = status; 2 = cleared)/jms
                elif control == '_20':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Device Linker _20 %s %s %s' % (node, action, eventInfo))
                        # Not relevant to Indigo, so ignored

# _21 = Z-Wave Events (actions and events defined in zwobjs.xsd)
# Z-Wave Events.  These are Z wave events about the management of the network, so they are not
# anything that we can propagate to/from the Indigo interface.  See Z-Wave API for 1.3 (System Status),
# 2.1/2.2/2.3/2.4/2.5 (Discovery Inactive/Include/Exclude/Replicate/Learn),
# 3.x.y General status and 4.x.y General Error.  Just ignore and log them.
#/jms/171220
                elif control == '_21':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Zwave event _21 %s %s' % (action, eventInfo))

# _22 = Billing Events (supported on ZS series.  Look in billobjs.xsd for information)
# According to the docs, we should be getting these on a "normal" ISY but we are
# and we're just going to log and ignore them all. /jms
                elif control == '_22':
                        self.plugin.pluginEventViewer(self.ISY, 'IGNORED Billing Event _22 %s %s %s' % (node, action, eventInfo))

# _23 = Portal Events (actions and event info defined in portal.xsd)
                elif control == '_23':
                        # don't propagate these to Indigo or log them
                        pass

		else:
			self.errorLog('UNKNOWN ISY control event control: %s action: %s eventInfo: %s' % (control, action, eventInfo))

	def handleRelayEvent(self, dev, control, action):
		self.debugLog('<<----called: handleRelayEvent')
# apparently,both 255 and 100 are legal for "on" for relay devices/jms.  At least
# all I am seeing is 100. 
		if control == 'ST':
			if action == '255':
				onOff = True
			elif action == '100':
				onOff = True
			else:
				onOff = False
			dev.updateStateOnServer('onOffState', onOff)
		else:
			self.errorLog('unhandled ISY relay event node %s control %s action %s' % (dev.address, control, action))
		
	def handleDimmerEvent(self, dev, control, action):
		self.debugLog('<<----called: handleDimmerEvent')
# we update the brightness based on the maximum, which is stored as a property of
# the device, and which we set earlier, typically either 255 (X10/Insteon) or 100 (ZWave)
# Indigo seems to automatically consider something with non-zero brightness as "ON," so
# we don't have to also tell the Indigo that it is turned on.  
# Note that "action" comes in as a string, so we have to mix strings, integers, and floats,
# and come out with an integer.  No kidding./jms/171220
		if control == 'ST':
			maxBrightness = dev.pluginProps['ISYmaxBrightness']
			#self.debugLog('handleDimmerEvent: dev maxBrightness is %d' % maxBrightness)
			newBrightness = int( float(action) * (100./float(maxBrightness)) )
			#self.debugLog('handleDimmerEvent: ST event new brightness %d' % newBrightness)
                        dev.updateStateOnServer('brightnessLevel', newBrightness)
		elif control == 'DON':
			# ignoring action, although it is usually 100 here
			# not sure how we tell Indigo what the brightness is, or if we even know./jms/171220
			# possibly for future analysis and experimentation.
			dev.updateStateOnServer('onOffState', True)
		elif control == 'DOF':
			# ignoring action, although is is usually 0 here
			dev.updateStateOnServer('onOffState', False)
		else:
			self.errorLog('unhandled ISY dimmer event node %s control %s action %s' % (dev.address, control, action))

	def handleThermostatEvent(self, dev, control, action):
		self.debugLog('<<----called: ISYThermostatEvent')
		if control == 'ST':
			dev.updateStateOnServer('temperatureInput1', int(action)/2)
			
		elif control == 'CLIMD':
			mode = action.replace(' ', '').replace('ProgramAuto', 'ProgramHeatCool')
			dev.updateStateOnServer('hvacOperationMode', mode)

		elif control == 'CLIHCS':
			if action == '0':
				dev.updateStateOnServer('hvacCoolerIsOn', False)
				dev.updateStateOnServer('hvacHeaterIsOn', False)
			elif action == '1':
				dev.updateStateOnServer('hvacCoolerIsOn', False)
				dev.updateStateOnServer('hvacHeaterIsOn', True)
			elif action == '2':
				dev.updateStateOnServer('hvacCoolerIsOn', True)
				dev.updateStateOnServer('hvacHeaterIsOn', False)
			else:
				self.errorLog('invalid CLIHCS action parameter')

		elif control == 'CLISPH':
			dev.updateStateOnServer('setpointHeat', int(action)/2)

		elif control == 'CLISPC':
			dev.updateStateOnServer('setpointCool', int(action)/2)

		elif control == 'CLIFS':
			if action == '7':
				dev.updateStateOnServer('hvacFanMode', 1)
			elif action == '8':
				dev.updateStateOnServer('hvacFanMode', 0)
			else:
				self.errorLog('invalid CLIFS action parameter')
				
		elif control == 'CLIHUM':
			# humidity - ignored for now
			pass
				
		elif control == 'UOM':
			# thermostat unit of measure - ignored for now
			pass

		else:
			self.errorLog('unhandled ISY thermostat event node %s control %s action %s' % (dev.address, control, action))
