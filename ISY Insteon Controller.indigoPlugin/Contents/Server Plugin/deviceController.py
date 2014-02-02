#! /usr/bin/env python
# -*- coding: utf-8 -*-
#							ISY INSTEON Controller
#							deviceController.py
#
#
# 2014/01/31 [JMM] Updated to address several issues:
#		* Subcat filtering was removed - doing some simple testing showed that the
#		  list was quite out of date. Downloaded the latest SDK and that one was
#		  also out of date though it's supposed to cover 4.0.5 (current shipping).
#		  The subcategory was used to look up the device type that's stuck into
#		  the description field so we're just going to skip that when it's not
#		  found in the 1_fam.xml file (supplied with the SDK).
#		* Apparently the API for executing programs changed - we can no longer 
#		  strip leading 0's from program IDs.
################################################################################

import indigo
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import httplib
import base64
import cgi
import simplejson
import os
import string
import struct

import urllib2
from xml.dom.minidom import parseString
import socket

from threading import Thread, Timer

#DiscoveryDone = False

kMulticastTimeout = 1.5

################################################################################
#
#		DEVICE CONTROLLER CLASS
#
################################################################################
	
class DeviceController(object):

	###########################
	# class init & del
	###########################

	def __init__(self, plugin):
		self.plugin = plugin
		self.debugLog = self.plugin.debugLog
		self.errorLog = self.plugin.errorLog
		self.discoveryDone = False
		self.deviceTypes = parseString(self.getXmlFromFile('1_fam.xml'))
		
	def __del__(self):
		pass

	def getXmlFromFile(self, filename):
		if not os.path.isfile(filename):
			return u""
		xml_file = file(filename, 'r')
		xml_data = xml_file.read()
		xml_file.close()
		return xml_data

################################################################################
#
#			ISY DISCOVERY VIA DATAGRAM
#
################################################################################
		
	def multicastTimeout(self):
		self.debugLog('<<--called: multicastTimeout')
		self.discoveryDone = True
		
	def deviceDiscovery(self):
		self.debugLog('<<---called: deviceDiscovery')
		
		ISYs = []
		MCAST_GRP = '239.255.255.250'
		MCAST_PORT = 1900
		msg = "M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nMAN:\"ssdp.discover\"\r\nMX:1\r\nST:urn:udi-com:device:X_Insteon_Lighting_Device:1\r\n\r\n"
		self.discoveryDone = False

		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
		sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 5)
		mreq = struct.pack('4sl', socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
		sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
		sock.settimeout(0.1)

		timer = Timer(kMulticastTimeout, self.multicastTimeout)
		timer.start()		
		sock.sendto(msg, (MCAST_GRP, MCAST_PORT))

		while self.discoveryDone != True:
			try:
				data = sock.recv(10240)
	 			if 'HTTP/1.1 200 OK' in data:
	 				index = data.index('LOCATION:')
	  				index2 = data.index('/desc')
 					location = data[index+16:index2]
 					index = data.index('USN:')
 					index2 = data.index('::urn')
 					uuid = data[index+4:index2]
 					ISY = '%s [%s]' % (location, uuid)
					self.debugLog(ISY)
					ISYs.append([ISY.replace(' ',''), ISY])
			except:
				pass

		sock.close()
		return ISYs

################################################################################
#
#			GET LIST OF ISY DEVICES
#
################################################################################
		
	def encodeBase64(self, arg):
		encoded = base64.b64encode(arg)
		self.debugLog(encoded)
		return encoded

	def sendRest(self, ISYIP, authorization, command):
		url = 'http://%s%s' % (ISYIP, command)
		self.debugLog(url)
		req = urllib2.Request(url)
		req.add_header('Authorization', 'Basic %s' % self.encodeBase64(authorization))
		file = urllib2.urlopen(req)
		response = file.read()
		file.close()
		return response

	def extractFromXML(self, xml, tag, attribute=None, value=None):
		elementList = xml.getElementsByTagName(tag)
		if attribute == None:
			return elementList[0].toxml().replace('<' + tag + '>','').replace('</' + tag + '>','')
			
		for element in elementList:
			if element.getAttribute(attribute) == value:
				return element
		
		return None # this is the failure case

	def parseOneDevice(self, device):
		flag = device.getAttribute('flag')
		name = self.extractFromXML(device, 'name')
		address = self.extractFromXML(device, 'address')
		type = self.extractFromXML(device, 'type').split('.')
		categoryID = type[0]
		subcategoryID = type[1]
		
		addressParts = address.split(' ')
		if addressParts[3] != '1' and categoryID != '4':   # this isn't a primary node or irrigation node
			return None					# only deal w/ root devices
#		these are the only categories supported
		if categoryID not in ['1', '2', '4', '5', '7']:    # tbd 16
			return None
		# Removed the subcategory check - testing shows that subcategories have expanded since
		# the plugin was originally written. The SDK hasn't been updated though so we also had
		# to tweak the description separator code 
		if categoryID == '1':
			# insteon dimmable devices - includes all currently supported by ISY
			deviceType = 'ISYDimmer'
		elif categoryID == '2':
			# insteon switch/relay devices - includes all currently supported by ISY
			deviceType = 'ISYRelay'
		elif categoryID == '3':
			# network bridges - not supported
			return None
		elif categoryID == '4' and int(subcategoryID) == 0:
			# insteon irrigation devices - includes all currently supported by ISY
			deviceType = 'ISYIrrigation'
		elif categoryID == '5':
			# insteon climate devices - currently only supports Venstar
			deviceType = 'ISYThermostat'
		elif categoryID == '6':
			# pool control - not supported
			return None
		elif categoryID == '7':
			# insteon sensors and actuators - currently only supports ioLinc
			deviceType = 'ISYIODevice'
		elif categoryID == '9':
			# energy management - not supported
			return None
		elif categoryID == '14':
			# windows/shades - not supported
			return None
		elif categoryID == '15':
			# access control/doors/locks - not supported
			return None
		elif categoryID == '16':
			# security/health/safety - currently not supported
			return None
		else:
			return None
		category = self.extractFromXML(self.deviceTypes, 'nodeCategory', 'id', categoryID)
		subcategory = self.extractFromXML(category, 'nodeSubCategory', 'id', subcategoryID)
		if subcategory:
			description = subcategory.getAttribute('name').replace('DEV_SCAT_','').replace('_',' ').lower()
			description = string.capwords(description)
		else:
			description = "Unknown Device"
		return {'name':name, 'address':address, 'type':deviceType, 'description':description, 'nodeType':flag}

	def getDevices(self, ISYIP, authorization):
		deviceXML = parseString(self.sendRest(ISYIP, authorization, '/rest/nodes'))
		devices = deviceXML.getElementsByTagName('node')
		deviceList = []
		for device in devices:
			deviceDict = self.parseOneDevice(device)
			if deviceDict != None:
				deviceList.append(deviceDict)
 		return deviceList
		
	def getScenes(self, ISYIP, authorization):
		sceneXML = parseString(self.sendRest(ISYIP, authorization, '/rest/nodes/scenes'))
		scenes = sceneXML.getElementsByTagName('group')
		sceneList = []
		for scene in scenes:
			name = self.extractFromXML(scene, 'name')
			address = self.extractFromXML(scene, 'address')
			sceneList.append([address, name])
		return sceneList
		
	def getPrograms(self, ISYIP, authorization):
		programXML = parseString(self.sendRest(ISYIP, authorization, '/rest/programs/?subfolders=true'))
		programs = programXML.getElementsByTagName('program')
		programList = []
		for program in programs:
			folder = program.getAttribute('folder')
			if folder == 'false':
				id = program.getAttribute('id')
				# Apparently the API changed and now requires the full id so we can't strip it. [JMM]
				#id = id.lstrip('0')
				name = self.extractFromXML(program, 'name')
				programList.append([id, name])
		return programList
		
################################################################################
#
#			ACTIONS
#
################################################################################

	def programCommand(self, ISYIP, authorization, id, cmd):
		command = '/rest/programs/%s/%s' % (id, cmd)
		self.sendRest(ISYIP, authorization, command)
		
	def queryStatus(self, ISYIP, authorization, address):
		command = '/rest/query/%s' % address.replace(' ', '%20')
		self.sendRest(ISYIP, authorization, command)

	######################
	# Process action request from Indigo Server for relays and dimmers.
	def deviceOn(self, ISYIP, authorization, address):
		command = '/rest/nodes/%s/cmd/DON/255' % address.replace(' ', '%20')
		self.sendRest(ISYIP, authorization, command)
		
	def deviceSetBrightness(self, ISYIP, authorization, address, bright100):
		bright255 = int(bright100 * 255/100)
		command = '/rest/nodes/%s/cmd/DON/%s' % (addres.replace(' ', '%20'), str(bright255))
		self.sendRest(ISYIP, authorization, command)

	def deviceOff(self, ISYIP, authorization, address):
		command = '/rest/nodes/%s/cmd/DOF' % address.replace(' ', '%20')
		self.sendRest(ISYIP, authorization, command)
		
	######################
	# Process action request from Indigo Server to change main thermostat's main mode.
	def changeHvacMode(self, ISYIP, authorization, address, newHvacMode):
		command = '/rest/nodes/%s/set/CLIMD/%s' % (address.replace(' ', '%20'), str(newHvacMode))
		self.sendRest(ISYIP, authorization, command)

	######################
	# Process action request from Indigo Server to change thermostat's fan mode.
	def changeFanMode(self, ISYIP, authorization, address, newFanMode):
		if newFanMode == 1:
			mode = '7'
		else:
			mode = '8'
		command = '/rest/nodes/%s/set/CLIFS/%s' % (address.replace(' ', '%20'), mode)
		self.sendRest(ISYIP, authorization, command)

	######################
	# Process action request from Indigo Server to change a cool setpoint.
	def changeCoolSetpoint(self, ISYIP, authorization, address, newSetpoint):
		if newSetpoint < 60.0:
			newSetpoint = 60.0
		elif newSetpoint > 85.0:
			newSetpoint = 85.0

		command = '/rest/nodes/%s/set/CLISPC/%s' % (address.replace(' ', '%20'), str(int(newSetpoint*2)))
		self.sendRest(ISYIP, authorization, command)

	######################
	# Process action request from Indigo Server to change a heat setpoint.
	def changeHeatSetpoint(self, ISYIP, authorization, address, newSetpoint):
		if newSetpoint < 60.0:
			newSetpoint = 60.0
		elif newSetpoint > 85.0:
			newSetpoint = 85.0

		command = '/rest/nodes/%s/set/CLISPH/%s' % (address.replace(' ', '%20'), str(int(newSetpoint*2)))
		self.sendRest(ISYIP, authorization, command)
