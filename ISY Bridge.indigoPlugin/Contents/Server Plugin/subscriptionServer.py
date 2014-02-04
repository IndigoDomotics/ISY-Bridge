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
		return elementList[0].toxml().replace('<' + tag + '>','').replace('</' + tag + '>','')
		
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
		
	def handleEvent(self, control, action, node, eventInfo):
		self.debugLog('<<----called: handleEvent')
		if control == 'ERR' or (control == '_3' and action == 'NE'):
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
				device = self.devices[node]
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
					self.debugLog("subscriptionServer.handleEvent: control: %s, action: %s" % (str(control), str(action)))
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

	def handleControlEvent(self, control, action, node, eventInfo):
		self.debugLog('<<----called: handleControlEvent')
		if control == '_0':
			# ISY heartbeat
			self.conn.send('beat')
 			self.heartbeatTimeout.cancel()
 			self.heartbeatTimeout = Timer(kHeartbeatTimeout, self.lostHeartbeat)
 			self.heartbeatTimeout.start()

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
			
			elif action == '3':
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
				
			else:
				self.errorLog('unhandled ISY control event node: %s control: %s action: %s eventInfo: %s' % (node, control, action, eventInfo))

		elif control == '_2':
			# driver specific events - ignored
			pass
			
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
				self.errorLog('unhandled ISY control event node: %s control: %s action: %s eventInfo: %s' % (node, control, action, eventInfo))

		elif control == '_4':
			# system config change - ignored for now
			pass
			
		elif control == '_5':
			# system status updated - ignored for now
			pass
			
		elif control == '_6':
			# internet access status - ignored for now
			pass

		elif control == '_7':
			# system updating - ignored for now
			pass
			
		elif control == '_8':
			# security system event - ignored for now
			pass
			
#		elif control == '_9':   # system alert event - not sure what this does, goes to error log for now

		elif control == '_10':
			# openADR and Flex Your Power events - ignored for now
			pass
			
		elif control == '_11':
			# climate events (requires weatherbug module on ISY) - ignored for now
			pass
			
		elif control == '_12':
			# AMI/SEP events (only applies to ISY Orchestrator series) - ignored for now
			pass
			
		elif control == '_13':
			# external energy monitoring events - ignored for now
			pass
			
		elif control == '_14':
			# ubp linker events - ignored for now
			pass
			
		elif control == '_15':
			# upb linker state - ignored for now
			pass
			
		elif control == '_16':
			# upb device status events - ignored for now
			pass
			
		elif control == '_17':
			# gas meter events (only applies to ISY Orchestrator series) - ignored for now
			pass
			
		elif control == '_18':
			# zigbee events (only applies to ISY Orchestrator series) - ignored for now
			pass
			
		elif control == '_19':
			# elk events (requires ISY Elk module) - ignored, use direct indigo/elk plugin
			pass

		else:
			self.errorLog('unhandled ISY control event control: %s action: %s eventInfo: %s' % (control, action, eventInfo))

	def handleRelayEvent(self, dev, control, action):
		self.debugLog('<<----called: handleRelayEvent')
		if control == 'ST':
			if action == '255':
				onOff = True
			else:
				onOff = False
			dev.updateStateOnServer('onOffState', onOff)
		else:
			self.errorLog('unhandled ISY relay event node %s control %s action %s' % (dev.address, control, action))
		
	def handleDimmerEvent(self, dev, control, action):
		self.debugLog('<<----called: handleDimmerEvent')
		if control == 'ST':
			dev.updateStateOnServer('brightnessLevel', int(action)*100/255)
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
