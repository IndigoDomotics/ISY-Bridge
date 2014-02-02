#ISY Bridge

This plugin will allow Indigo to control devices through an ISY controller. You can also execute scenes and programs. The original author of the plugin gave us permission to open source the code for this plugin.

Several fixes were made to the plugin to make it compatible (and, in fact, it ***requires***) Indigo 6. Also, there were some changes to the ISY REST API which have been incorporated. This plugin has been tested with the following ISY Controllers and firmware:

| ISY Controller | Firmware Version(s) |
|:--------------:|:-------------------:|
| ISY-944i | v4.0.5 |

##Downloading for use

If you are a user and just want to download and install the plugin, click on the **Download ZIP** button to the right and it will download the plugin and readme file to a folder in your Downloads directory called **ISY-Bridge**. Once it's downloaded just open that folder and double-click on the **ISY Bridge.indigoPlugin** file to have the client install and enable it for you.

##How to use

Once you've installed the plugin, you'll need to create an ISY Controller device. Create a new device, select **ISY Bridge** from the **Type:** popup, then select **ISY Controller** from the **Model:** popup. You'll get a config dialog where you can select your ISY from a popup list and add authentication details. When you save the config dialog, the plugin will automatically talk to the ISY to get a list of all supported devices and it will automatically create Indigo devices representing each one. It will make them in the same folder where you created the ISY Controller device.

If you later add a device, you can select the **Plugins->ISY Bridge->Update ISY** menu item and you'll be able to hit a button to update devices (it will add any that aren't present). It will also rebuild the list of scenes and programs.

The plugin presents 2 events: when a program finishes the THEN portion of a program or when a program finishes the ELSE portion of a program. ISY users will understand this.

The plugin also presents 3 actions: **Send Scene On Command**, **Send Scene Off Command**, **Send Program Command**. These are pretty self-explanatory.

Currently supported device types are: *Dimmers*, *On/Off (relay) devices*, *Sprinklers*, *Thermostats*, and the *I/O Linc*. We've added an enhancement issue to expand that list but it'll have to be done by someone else.

##Contributing

If you want to contribute, just clone the repository in your account, make your changes, and issue a pull request. Make sure that you describe the change you're making thoroughly - this will help the repository managers accept your request more quickly.

##Terms

Perceptive Automation is hosting this repository and will do minimal management. Unless a pull request has no description or upon cursory observation has some obvious issue, pull requests will be accepted without any testing by us. We may choose to delegate commit privledges to other users at some point in the future.

We (Perceptive Automation) don't guarantee anything about this plugin - that this plugin works or does what the description above states, so use at your own risk. We will attempt to answer questions about the plugin but note that since we don't use it regularly we may not have the answers. We certainly can't really help with questions about your ISY.

