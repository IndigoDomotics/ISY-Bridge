#! /usr/bin/env python
# -*- coding: utf-8 -*-
#							ISY Bridge
#							plugin.py
#
# 2014/01/31 [JMM] Updated to address several issues:
#		* Device names from the ISY might collide with devices already in Indigo
#		  so we needed to append a number to differentiate (since you can't have
#		  duplicate names in Indigo). getName() method was added to do this.
#		* I removed the auto renaming that occurred whenever the name was changed
#		  in the ISY - first, the new name could collide (though we could deal with
#		  that) and secondly the user might want to keep the name they gave it in
#		  Indigo.
#		* I also removed the automatic move back to the folder where the device's
#		  parent ISY Controller device is. If the user moved it to a different
#		  folder it's likely that they want it to stay there.
#		* In the Devices.xml file, changed "not connected" to "disconnected" on
#		  the ISY Controller device - this will turn the icon to the gray dot when
#		  there's no communication with it or it's disabled.
#		* Removed the deviceCreated() method (which works differently under Indigo 6
#		  and replaced the functionality with a flag in the device props.
#		* Updated the Info.plist with a bunch of new information to reflect the
#		  open source nature of the plugin, the new name, help, etc.
################################################################################

import time

import socket
import simplejson
from threading import Thread
from deviceController import DeviceController
from subscriptionServer import SubscriptionServer
from xml.dom.minidom import parseString

################################################################################
#
#		INDIGO PLUGIN CLASS
#
################################################################################

class Plugin(indigo.PluginBase):

	###########################
	# class init & del
	###########################

	def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
		indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
		self.debug = pluginPrefs.get('debug', False)
		self.eventViewer = pluginPrefs.get('eventViewer', 'none')
		self.debugLog('<<----called: init')
		self.deviceController = DeviceController(self)
		self.lookupTable = {}
		self.thenTriggers = {}
		self.elseTriggers = {}
		
	def __del__(self):
		indigo.PluginBase.__del__(self)
				
	###########################
	# validate plugin Prefs
	###########################

	def validatePrefsConfigUi(self, valuesDict):
		self.debugLog('<<----called: validatePrefsConfigUi')
		self.debug = valuesDict['debug']
		self.eventViewer = valuesDict['eventViewer']
		return True
	
##################################################################################
#
#		UTILITY METHODS
#
##################################################################################

	###########################
	# find a valid device name
	###########################
	def getName(self, curName):
		if curName in indigo.devices:
			try:
				curNameParts = curName.split(" ")
				if len(curNameParts) == 1:
					curNameParts.append("1")
				else:
					versionNum = int(curNameParts[-1])
					curNameParts[-1] = str(versionNum+1)
			except:
				curNameParts[-1] = str(versionNum+1)
			curName = " ".join(curNameParts)
			return self.getName(curName)
		return curName

##################################################################################
#
#		DEVICE OPERATING METHODS
#
##################################################################################

	###########################
	# device start communication
	###########################

	def deviceStartComm(self, dev):
		# This is called for each device that is ours, but we only pay attention if it is the ISY
		# device itself.  All of "our" other devices are just ignored (with the return below)
		self.debugLog('<<----called: deviceStartComm:  %s' % dev.name)
		if dev.deviceTypeId != 'ISY': return
		
		dev.updateStateOnServer('badNodesList', '[]')
		pluginProps = dev.pluginProps
		#self.debugLog("pluginprops are %s" % pluginProps)
		
		# Added flag to the props for this device that will allow us to do
		# the initial load of devices, scenes, and programs when an ISY Controller
		# device is first started up. [JMM]
		if not pluginProps['initialLoadDone']:
			self.updateISYDevices(dev)
			self.updateISYScenes(dev)	
			self.updateISYPrograms(dev)
			pluginProps['initialLoadDone'] = True
			dev.replacePluginPropsOnServer(pluginProps)
 		
 		# create index of devices and initialize subscription server
		#self.debugLog('deviceStartComm: Setting up deviceDict, then dumping it.')
		deviceDict = dict([(device.address, device) for device in indigo.devices if device.pluginId == self.pluginId
			and device.deviceTypeId != 'ISY' and device.pluginProps['ISYuuid'] == pluginProps['ISYuuid']])
		#self.debugLog(deviceDict)
		subscriptionServer = SubscriptionServer(self, dev, deviceDict)

		# pass the port to the Subscription Manager and start it
 		myThread = Thread(target=subscriptionServer.startServer)
 		myThread.start()
			
		# create entry in lookup table
		self.lookupTable[pluginProps['ISYuuid']] = {'ISYIP':dev.address, 'authorization':pluginProps['authorization'], 'subscriptionServer':subscriptionServer, 'thread':myThread}

	###########################
	# device stop communication
	###########################

	def deviceStopComm(self, dev):
		self.debugLog('<<----called: deviceStopComm: %s id: %d' % (dev.name, dev.id))
		if dev.deviceTypeId == 'ISY':
			ISYuuid = dev.pluginProps['ISYuuid']
			lookup = self.lookupTable[ISYuuid]
			subscriptionServer = lookup['subscriptionServer']
			myThread = lookup['thread']
			del self.lookupTable[ISYuuid]
			subscriptionServer.stopServer()
			time.sleep(4)
			myThread.join()
			indigo.server.log('ISY stopped')

##################################################################################
#
#		ISY DEVICE CONFIGURATION
#
##################################################################################

	###########################
	# device configUi methods
	###########################

	# method for dynamic list of ISYs from device configUi
	def populateISYList(self, filter='', valuesDict=None, typeId='', targetId=0):
		self.debugLog('<<----called: populateISYList')
		
		ISYInfoList = self.deviceController.deviceDiscovery()
		self.debugLog(simplejson.dumps(ISYInfoList))
		
		return ISYInfoList
	
	# callback method for findISY button		
	def findISYButton(self, valuesDict, typeId, devID):
		self.debugLog('<<----called: findISYButton')
		# just need to return valuesDict to get ISYList to auto repopulate
		return valuesDict

	###########################
	# validate configUi input
	###########################

	def validateDeviceConfigUi(self, valuesDict, typeId, devId):
		self.debugLog('<<----called: validatePrefsConfigUi')
		errorsDict = indigo.Dict()
		
		if typeId != 'ISY': return (False, valuesDict, errorsDict)
		
		if valuesDict['ISYSelection'] == '':
			errorsDict['ISYSelection'] = 'You must select an ISY.'

		if len(errorsDict) > 0:
			self.debugLog('<------exiting: validatePrefsConfigUi --->user input is not valid')
			return (False, valuesDict, errorsDict)
		else:
			self.debugLog('<------exiting: validatePrefsConfigUi --->user input is valid')
			ISYSelection = valuesDict['ISYSelection']
			self.debugLog('validateDeviceConfigUi: selected ISY controller: %s' % ISYSelection)
			i = ISYSelection.index('[')
			valuesDict['address'] = ISYSelection[:i]
			valuesDict['ISYuuid'] = ISYSelection[i+1:].replace(']','')
			valuesDict['authorization'] = '%s:%s' % (valuesDict['username'], valuesDict['password'])
			return (True, valuesDict)
		
	###########################
	# communication overrides
	###########################
	
	def didDeviceCommPropertyChange(self, origDev, newDev):
		self.debugLog('<<----called: didDeviceCommPropertyChange')
		# conditions requiring device restart
		if origDev.address != newDev.address:
			return True
		else:
			return False
	
	###########################
	# device update methods
	###########################

	def updateISYDevices(self, dev):

		folderId = dev.folderId
		ISYIP = dev.address   #pluginProps['ISYIP']
		pluginProps = dev.pluginProps
		ISYuuid = pluginProps['ISYuuid']

		# create, update or delete indigo devices for ISY Insteon devices
		ISYDevices = self.deviceController.getDevices(ISYIP, pluginProps['authorization'])
		self.debugLog("ISYDevices:\n%s" % str(ISYDevices))
		self.debugLog("jms/end of ISYDevices")

		existingDevices = [device for device in indigo.devices
			if device.pluginId == self.pluginId and device.pluginProps['ISYuuid'] == ISYuuid
			and device.deviceTypeId != "ISY" and device.deviceTypeId != 'ISYProgram']
		for existingDevice in existingDevices:
			# check to see if this device corresponds to a device on the new device list
			newDevice = [device for device in ISYDevices if device['address'] == existingDevice.pluginProps['address']]
			if newDevice != []:
				deviceInfo = newDevice[0]
				self.debugLog('updating ISY device: %s' % existingDevice.name)
				
				# I don't believe trying to sync the name from the ISY to Indigo
				# is a reasonable thing to do given that the new name might
				# collide with an existing device in Indigo. After initial
				# creation, it'll be up to the user to change the name in
				# Indigo. [JMM]
				#existingDevice.name = deviceInfo['name']
				
				existingDevice.description = deviceInfo['description']
				existingDevice.replaceOnServer()
				
				# I also don't believe that a device should automatically
				# move back to the folder that it's controller device is
				# in. If the user moved it, they probably would like for
				# it to stay moved. [JMM]
				#if existingDevice.folderId != folderId:
				#	existingDevice.moveToFolder(folderId)
				
				# remove the device address and info from the list we need to deal with
				ISYDevices = [dev for dev in ISYDevices if dev != deviceInfo]
			else:
				# if the device is not in the new device list, delete it
				self.debugLog('deleting device: %s' % existingDevice.name)
				indigo.device.delete(existingDevice.id)

		# add devices if necessary
		# jms/171220 - added a number of properties, including ISYmaxBrightness and ISYtype, so
		# that we can "know" more about the device when we come back to handling it when we get
		# an event.  Fortunately, Indigo is great about letting us store junk in their database.
		for device in ISYDevices:
			self.debugLog('creating device: %s' % device['name'])
			pDev = indigo.device.create(protocol=indigo.kProtocol.Plugin,
				# We need to get a valid name so that an exception isn't thrown if
				# the ISY name for the device collides with an existing device in
				# Indigo. [JMM]
				name=self.getName(device['name']),
				deviceTypeId=device['type'],
				description=device['description'],
				props={'ISYuuid':ISYuuid, 'address':device['address'], 'ISYtype':device['type'], 
				 'ISYmaxBrightness':device['maxBrightness'], 'ShowCoolHeatEquipmentStateUI':True},
				 folder=folderId)

	def updateISYScenes(self, dev):
		ISYIP = dev.address
		pluginProps = dev.pluginProps
		ISYScenes = self.deviceController.getScenes(ISYIP, pluginProps['authorization'])
		pluginProps['scenes'] = simplejson.dumps(ISYScenes)
		self.debugLog("scene list from ISY: \n%s" % str(pluginProps['scenes']))
		dev.replacePluginPropsOnServer(pluginProps)

	def updateISYPrograms(self, dev):
		ISYIP = dev.address
		pluginProps = dev.pluginProps
		ISYPrograms = self.deviceController.getPrograms(ISYIP, pluginProps['authorization'])
		pluginProps['programs'] = simplejson.dumps(ISYPrograms)
		self.debugLog("program list from ISY: \n%s" % str(pluginProps['programs']))
		dev.replacePluginPropsOnServer(pluginProps)
		
	###########################
	# device deleted
	###########################

	def deviceDeleted(self, dev):
		self.debugLog('<<----called: deviceDeleted')
		if dev.deviceTypeId == 'ISY':
			ISYuuid = dev.pluginProps['ISYuuid']
			[indigo.device.delete(device.id) for device in indigo.devices
				if device.pluginId == self.pluginId and device.pluginProps['ISYuuid'] == ISYuuid
				and device.deviceTypeId != 'ISY']
		else:
			pluginProps = dev.pluginProps
			address = pluginProps['address']
			ISYuuid = pluginProps['ISYuuid']
			if ISYuuid in self.lookupTable:
				self.lookupTable[ISYuuid]['subscriptionServer'].deleteDevice(address)

		self.deviceStopComm(dev)

##################################################################################
#
#		CALLBACKS FROM SUBSCRIPTION SERVER
#
##################################################################################

	def communicationError(self, ISY, dev):
		self.debugLog('<<---called: communicationError: %s' % dev.name)
		indigo.device.enable(dev, value=False)
		dev.updateStateOnServer('communicationError', True)
		badNodesList = simplejson.loads(ISY.states['badNodesList'])
		if dev.address not in badNodesList:
			badNodesList.append(dev.address)
		ISY.updateStateOnServer('badNodesList', simplejson.dumps(badNodesList))
		return False
	
	def communicationResumed(self, ISY, dev):
		self.debugLog('<<---called: communicationResumed: %s' % dev.name)
		indigo.device.enable(dev, value=True)
		dev.updateStateOnServer('communicationError', False)
		badNodesList = simplejson.loads(ISY.states['badNodesList'])
		if dev.address in badNodesList:
			badNodesList = [node for node in badNodesList if node != dev.address]
		ISY.updateStateOnServer('badNodesList', simplejson.dumps(badNodesList))
		
	def deviceNeedsDeletion(self, dev):
		self.debugLog('<<---called: deviceNeedsDeletion: %s' % dev.name)
		indigo.device.delete(dev.id)
		
	def deviceNeedsAdding(self, ISY, devStr):
		self.debugLog('<<---called: deviceNeedsAdding: %s' % devStr)
		ISYuuid = ISY.pluginProps['ISYuuid']
		folderId = ISY.folderId
		devXML = parseString(devStr).getElementsByTagName('node')[0]
		device = self.deviceController.parseOneDevice(devXML)
		if device == None:
			return None
		else:
			pDev = indigo.device.create(protocol=indigo.kProtocol.Plugin,
				name=device['name'],
				deviceTypeId=device['type'],
				description=device['description'],
				props={'ISYuuid':ISYuuid, 'address':device['address'], 'ShowCoolHeatEquipmentStateUI':True},
				folder=folderId)
			if device['nodeType'] == '144': indigo.device.enable(pDev, value=False)
			return pDev
		
	def undefinedDeviceDetected(self, address):
		self.debugLog('<<---called: undefinedDeviceDetected: %s' % address)
		
	def programFeedback(self, id, fork, status):
		self.debugLog('program: %s %s fork %s execution' % (id, fork, status))
		if fork == 'then':
			if status == 'finished':
				if id in self.thenTriggers:
					indigo.trigger.execute(self.thenTriggers[id])
		elif fork == 'else':
			if status == 'finished':
				if id in self.elseTriggers:
					indigo.trigger.execute(self.elseTriggers[id])

	def pluginEventViewer(self, ISY, eventInfo):
		if self.eventViewer == 'normal':
			indigo.server.log('[%s Event Viewer]: %s' % (ISY.name, eventInfo))
		elif self.eventViewer == 'highlight':
			self.errorLog('[%s Event Viewer]: %s' % (ISY.name, eventInfo))

	def queryDevice(self, ISY, address):
		self.deviceController.queryStatus(ISY.address, ISY.pluginProps['authorization'], address)

##################################################################################
#
#		MENU ITEMS
#
##################################################################################

	def updateAllFromMenu(self, valuesDict, typeId):
		self.debugLog('<<---called: updateAllFromMenu')
		if valuesDict['ISYSelection'] == '': return
		dev = valuesDict['ISYSelection']
		self.updateISYDevices(indigo.devices[int(dev)])
		self.updateISYScenes(indigo.devices[int(dev)])
		self.updateISYPrograms(indigo.devices[int(dev)])

	def updateDevicesFromMenu(self, valuesDict, typeId):
		self.debugLog('<<---called: updateDevicesFromMenu')
		if valuesDict['ISYSelection'] == '': return
		dev = valuesDict['ISYSelection']
		self.updateISYDevices(indigo.devices[int(dev)])

	def updateScenesFromMenu(self, valuesDict, typeId):
		self.debugLog('<<---called: updateScenesFromMenu')
		if valuesDict['ISYSelection'] == '': return
		dev = valuesDict['ISYSelection']
		self.updateISYScenes(indigo.devices[int(dev)])

	def updateProgramsFromMenu(self, valuesDict, typeId):
		self.debugLog('<<---called: updateProgramsFromMenu')
		if valuesDict['ISYSelection'] == '': return
		dev = valuesDict['ISYSelection']
		self.updateISYPrograms(indigo.devices[int(dev)])
		
##################################################################################
#
#		ACTION METHODS
#
##################################################################################

	###########################
	# validate action configUi input
	###########################

	def validateActionConfigUi(self, valuesDict, typeId, actionId):
		errorsDict = indigo.Dict()
		if typeId == 'sendProgramCommand':
			if valuesDict['program'] == '':
				errorsDict['program'] = 'You must select a program.'
			if valuesDict['command'] == '':
				errorsDict['command'] = 'You must select a command.'
		else:
			if valuesDict['scene'] == '':
				errorsDict['scene'] == 'You must select a scene.'
		
		if len(errorsDict) > 0:
			return (False, valuesDict, errorsDict)
		else:
			return (True, valuesDict)

	# method for dynamic list of ISY scenes
	def populateSceneList(self, filter='', valuesDict=None, typeId='', targetId=0):
		self.debugLog('<<----called: populateSceneList')
		ISYList = [device for device in indigo.devices if device.pluginId == self.pluginId
			and device.deviceTypeId == 'ISY']
		numberOfISYs = len(ISYList)
		menu = []
		for ISY in ISYList:
			scenes = simplejson.loads(ISY.pluginProps['scenes'])
			for scene in scenes:
				if numberOfISYs == 1:
					menu.append(['[%s]%s' % (ISY.id, scene[0]), scene[1]])
				else:
					menu.append('[%s]%s' % (ISY.id, scene[0]), '[%s]%s' % (ISY.name, scene[1]))
		return menu

	# method for dynamic list of ISY programs
	def populateProgramList(self, filter="", valuesDict=None, typeId="", targetId=0):
		self.debugLog('<<----called: populateProgramList')
		ISYList = [device for device in indigo.devices if device.pluginId == self.pluginId
			and device.deviceTypeId == 'ISY']
		numberOfISYs = len(ISYList)
		self.debugLog("populateProgramsList: numberOfISYs: %i" % numberOfISYs)
		menu = []
		for ISY in ISYList:
			programs = simplejson.loads(ISY.pluginProps['programs'])
			for program in programs:
				if numberOfISYs == 1:
					menu.append(['[%s]%s' % (ISY.id, program[0]), program[1]])
				else:
					menu.append('[%s]%s' % (ISY.id, program[0]), '[%s]%s' % (ISY.name, program[1]))
		self.debugLog("populateProgramList: menu: %s" % str(menu))
		return menu

	###########################
	# custom action methods
	###########################

	def sendSceneOn(self, action):
		scene = action.props['scene']
		indigo.server.log("scene value: |%s|" % str(scene), type="ISY Debug")
		index = scene.index(']')
		ISYId = int(scene[1:index])
		sceneAddress = scene[index+1:]
		dev = indigo.devices[ISYId]
		self.deviceController.deviceOn(dev.address, dev.pluginProps['authorization'], sceneAddress)

	def sendSceneOff(self, action):
		scene = action.props['scene']
		self.debugLog("sendSceneOff: scene value: %s" % str(scene))
		index = scene.index(']')
		ISYId = int(scene[1:index])
		sceneAddress = scene[index+1:]
		dev = indigo.devices[ISYId]
		self.deviceController.deviceOff(dev.address, dev.pluginProps['authorization'], sceneAddress)

	def sendProgramCommand(self, action):
		program = action.props['program']
		index = program.index(']')
		ISYId = int(program[1:index])
		programId = program[index+1:]
		dev = indigo.devices[ISYId]
		self.deviceController.programCommand(dev.address, dev.pluginProps['authorization'], programId, action.props['command'])
	
	########################################
	# Dimmer/Relay Action callback
	########################################
	def actionControlDimmerRelay(self, action, dev):
		pluginProps = dev.pluginProps
		address = pluginProps['address']
		ISYuuid = pluginProps['ISYuuid']
		ISYtype = pluginProps['ISYtype']
		ISYmaxBrightness = pluginProps['ISYmaxBrightness']
		ISYIP = self.lookupTable[ISYuuid]['ISYIP']
		self.debugLog("In actionControlDimmerRelay/jms")
		self.debugLog("address %s ISYuuid %s ISYtype %s ISYIP %s" % (address, ISYuuid, ISYtype, ISYIP))
		authorization = self.lookupTable[ISYuuid]['authorization']

		###### TURN ON ######
		if action.deviceAction == indigo.kDeviceAction.TurnOn:
			if ISYtype == 'ISYRelay':
				self.deviceController.deviceOn(ISYIP, authorization, address)
			else:
				self.deviceController.deviceOnDimmer(ISYIP, authorization, address, ISYmaxBrightness)

		###### TURN OFF ######
		elif action.deviceAction == indigo.kDeviceAction.TurnOff:
			self.deviceController.deviceOff(ISYIP, authorization, address)

		###### TOGGLE ######
		elif action.deviceAction == indigo.kDeviceAction.Toggle:
			if dev.onState:
				self.deviceController.deviceOff(ISYIP, authorization, address)
			else:
				self.deviceController.deviceOn(ISYIP, authorization, address)

		###### SET BRIGHTNESS ######
		elif action.deviceAction == indigo.kDeviceAction.SetBrightness:
			self.deviceController.deviceSetBrightness(ISYIP, authorization, address, action.actionValue, ISYmaxBrightness)
				
		###### BRIGHTEN BY ######
		elif action.deviceAction == indigo.kDeviceAction.BrightenBy:
			currentBrightness = int(dev.states['brightnessLevel'])
			newBrightness = currentBrightness + int(action.actionValue)
			self.deviceController.deviceSetBrightness(ISYIP, authorization, address, newBrightness, ISYmaxBrightness)

		###### DIM BY ######
		elif action.deviceAction == indigo.kDeviceAction.DimBy:
			currentBrightness = int(dev.states['brightnessLevel'])
			newBrightness = currentBrightness - int(action.actionValue)
			self.deviceController.deviceSetBrightness(ISYIP, authorization, address, newBrightness, ISYmaxBrightness)
			
		###### STATUS REQUEST ######
#		elif action.deviceAction == indigo.kDeviceAction.RequestStatus:
			self.deviceController.queryStatus(ISYIP, authorization, address)

	########################################
	# Thermostat Action callback
	########################################
	# Main thermostat action bottleneck called by Indigo Server.
	def actionControlThermostat(self, action, dev):
		pluginProps = dev.pluginProps
		address = pluginProps['address']
		ISYuuid = pluginProps['ISYuuid']
		ISYIP = self.lookupTable[ISYuuid]['ISYIP']
		authorization = self.lookupTable[ISYuuid]['authorization']

		###### SET HVAC MODE ######
		if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
			self.deviceController.changeHvacMode(ISYIP, authorization, address, action.actionMode)

		###### SET FAN MODE ######
		elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
			self.deviceController.changeFanMode(ISYIP, authorization, address, action.actionMode)

		###### SET COOL SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
			newSetpoint = action.actionValue
			self.deviceController.changeCoolSetpoint(ISYIP, authorization, address, newSetpoint)

		###### SET HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
			newSetpoint = action.actionValue
			self.deviceController.changeHeatSetpoint(ISYIP, authorization, address, newSetpoint)

		###### DECREASE/INCREASE COOL SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
			newSetpoint = dev.coolSetpoint - action.actionValue
			self.deviceController.changeCoolSetpoint(ISYIP, authorization, address, newSetpoint)

		elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
			newSetpoint = dev.coolSetpoint + action.actionValue
			self.deviceController.changeCoolSetpoint(ISYIP, authorization, address, newSetpoint)

		###### DECREASE/INCREASE HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint - action.actionValue
			self.deviceController.changeHeatSetpoint(ISYIP, authorization, address, newSetpoint)

		elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint + action.actionValue
			self.deviceController.changeHeatSetpoint(ISYIP, authorization, address, newSetpoint)

		###### REQUEST STATE UPDATES ######
  		elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
  		indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
  		indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
			self.deviceController.queryStatus(ISYIP, authorization, address)
		
##################################################################################
#
#		EVENT METHODS
#
##################################################################################

	###########################
	# validate event configUi input
	###########################

	def validateEventConfigUi(self, valuesDict, typeId, eventId):
		errorsDict = indigo.Dict()
		if valuesDict['program'] == '':
			errorsDict['program'] = 'You must select a program.'
			return (False, valuesDict, errorsDict)
		else:
			return (True, valuesDict)
		
	########################################
	def triggerStartProcessing(self, trigger):
		self.debugLog("Start processing trigger: %i" % trigger.id)
		triggerType = trigger.pluginTypeId
		programId = trigger.pluginProps['program']
		if triggerType == 'programThenFinished':
			self.thenTriggers[programId] = trigger
		elif triggerType == 'programElseFinished':
			self.elseTriggers[programId] = trigger
	
	########################################
	def triggerStopProcessing(self, trigger):
		self.debugLog("Stop processing trigger " + str(trigger.id))
		triggerType = trigger.pluginTypeId
		programId = trigger.pluginProps['program']
		if triggerType == 'programThenFinished':
			del self.thenTriggers[programId]
		elif triggerType == 'programElseFinished':
			del self.elseTriggers[programId]
