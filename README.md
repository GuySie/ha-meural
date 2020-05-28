# ha-meural
**Integration for Meural Canvas digital art frame in Home Assistant**  

*Last master update: 28 May 2020*  
*Previous master update: 26 May 2020*  

The Netgear Meural Canvas is a digital art frame with both a local interface and a cloud API.  
Home Assistant is an open source home automation package that puts local control and privacy first.  
This integration leverages Meural's API and local interface to control the Meural Canvas as a media player in Home Assistant.  

# Installation
Copy the 'meural' folder into your Home Assistant's 'custom_components' folder and restart Home Assistant. Log in with your Netgear account when setting up the integration. The integration will detect all Canvas devices registered to your account. Each Canvas will become a Media Player entity and can be added to your Lovelace UI using any component that supports it, for example the default Media Control card.  

The integration supports built-in media player service calls to pause, play, go to next/previous track (artwork), select source (art playlist), set shuffle, turn on and turn off. Additional services built into this integration are:  
*meural.set_device_option*  
*meural.set_brightness*  
*meural.reset_brightness*  
*meural.toggle_informationcard*  
These services are fully documented in services.yaml.

# Meural API
Meural has a REST API that their mobile app and web-interface run on. Unofficial information on this API can be found here:
https://documenter.getpostman.com/view/1657302/RVnWjKUL?version=latest

# Local Web Server
Netgear refers to a 'remote controller' in their Meural support documentation:  
https://kb.netgear.com/000060746/Can-I-control-the-Canvas-without-a-mobile-app-or-gesture-control-and-if-so-how  
This 'remote controller' is a local web server on the Canvas device available at: http://LOCALIP/remote/  
It runs on a javascript available at: http://LOCALIP/static/remote.js

The available calls in this javascript are:  
*/remote/identify/*  
*/remote/get_galleries_json/*  
*/remote/get_gallery_status_json/*  
*/remote/get_frame_items_by_gallery_json/*  
*/remote/get_wifi_connections_json/*  
*/remote/get_backlight/*  
*/remote/control_check/sleep/*  
*/remote/control_check/video/*  
*/remote/control_check/als/*  
*/remote/control_check/system/*  
*/remote/control_command/boot_status/image/*  
*/remote/control_command/set_key/*  
*/remote/control_command/set_backlight/*  
*/remote/control_command/suspend*  
*/remote/control_command/resume*  
*/remote/control_command/set_orientation/*  
*/remote/control_command/change_gallery/*  
*/remote/control_command/change_item/*  
*/remote/control_command/rtc/*  
*/remote/control_command/language/*  
*/remote/control_command/country/*  
*/remote/control_command/als_calibrate/off/*  
*/remote/control_command_post/connect_to_new_wifi/*  
*/remote/control_command_post/connect_to_exist_wifi/*  
*/remote/control_command_post/connect_to_hidden_wifi/*  
*/remote/control_command_post/delete_wifi_connection/*  
*/remote/postcard/*  

# Google Assistant
Meural currently only supports Alexa voice commands for the Canvas. However, if your Home Assistant supports Google Assistant - either configured manually or via Nabu Casa - you can expose the Canvas entity and control it via Google Assistant. A media player in Home Assistant exposes OnOff and Modes to Google. This means you can turn the Canvas on or off and select different playlists for the Canvas to display. Change the name of your Canvas to something you can pronounce - if you want to call your Canvas 'Meural', spell it 'Mural'.  

For example, say:  
*"Hey Google, turn on (canvas name)."*  
*"Hey Google, set input source to (playlist name) on (canvas name)."*  
*"Hey Google, turn off (canvas name)."*  

A proof of concept video can be found here:
https://twitter.com/GuySie/status/1265349696119283716

# Thanks
The first version of this integration was built by [@balloob](https://github.com/balloob) - many, many thanks to him. Blame [@guysie](https://github.com/guysie) for all code added afterwards. I'm not a dev, so I apologize in advance for code quality.  