# HA-meural
**Integration for Meural Canvas digital art frame in Home Assistant**  

*Last master update: x June 2020*  
*Previous master update: 3 June 2020*  

The Netgear Meural Canvas is a digital art frame with both a local interface and a cloud API.  
Home Assistant is an open source home automation package that puts local control and privacy first.  
This integration leverages Meural's API and local interface to control the Meural Canvas as a media player in Home Assistant.  

## Installation
Copy the `meural` folder inside `custom_components` to your Home Assistant's `custom_components` folder and restart Home Assistant. Go to *Configuration*, *Integrations*, click the + to add a new integration and find the Meural integration to set up. Log in with your Netgear account. The integration will detect all Canvas devices registered to your account. Each Canvas will become a Media Player entity and can be added to your Lovelace UI using any component that supports it, for example the default Media Control card. The entity will correspond to the name you have given the Canvas. By default your Canvas has a name consisting of a painter's name and 3 digits like `picasso-428`, which would result in the entity `media_player.picasso-428` being created.

![Meural Canvas in Media Control card](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/mediacontrolcard.png)

The integration supports built-in media player service calls to pause, play, play a specific item, go to the next/previous track (artwork), select a source (art playlist), set shuffle mode, and turn on or turn off:  
`media_player.media_pause`  
`media_player.media_play`  
`media_player.play_media`  
`media_player.media_next_track`  
`media_player.media_previous_track`  
`media_player.select_source`  
`media_player.shuffle_set`  
`media_player.turn_on`  
`media_player.turn_off`  

![Meural Canvas in entity settings](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/entitysettings.png)

Additional services built into this integration are:  
`meural.set_device_option`  
`meural.set_brightness`  
`meural.reset_brightness`  
`meural.toggle_informationcard`  
`meural.preview_image`  
These services are fully documented in `services.yaml`.

Service `meural.preview_image` temporarily displays an image on your Canvas. The amount of time preview images will display can be set with `previewDuration` using service `meural.set_device_option`, in the Meural app or in the Meural web interface. This service is most suitable for use in automation when you wish to display non-artwork items temporarily on the Canvas.

**Tip:** The official Meural settings for the sensitivity of brightness to ambient light sensor reading are limited to high (100), medium (20) or low (4). But you can make it any value of sensitivity, on a scale of 0 to 100, using `meural.set_device_option` and setting `alsSensitivity`. I find Meural's low value will still make the screen too bright, so I keep mine set to 2.

## Meural API
Meural has a REST API that their mobile app and web-interface run on. Unofficial documentation on this API can be found here:
https://documenter.getpostman.com/view/1657302/RVnWjKUL?version=latest

## Local Web Server
Netgear refers to a 'remote controller' in their Meural support documentation:  
https://kb.netgear.com/000060746/Can-I-control-the-Canvas-without-a-mobile-app-or-gesture-control-and-if-so-how  
This 'remote controller' is a local web server on the Canvas device available at: http://LOCALIP/remote/  
It runs on a javascript available at: http://LOCALIP/static/remote.js

The available calls in this javascript are:  
`/remote/identify/`  
`/remote/get_galleries_json/`  
`/remote/get_gallery_status_json/`  
`/remote/get_frame_items_by_gallery_json/`  
`/remote/get_wifi_connections_json/`  
`/remote/get_backlight/`  
`/remote/control_check/sleep/`  
`/remote/control_check/video/`  
`/remote/control_check/als/`  
`/remote/control_check/system/`  
`/remote/control_command/boot_status/image/`  
`/remote/control_command/set_key/`  
`/remote/control_command/set_backlight/`  
`/remote/control_command/suspend`  
`/remote/control_command/resume`  
`/remote/control_command/set_orientation/`  
`/remote/control_command/change_gallery/`  
`/remote/control_command/change_item/`  
`/remote/control_command/rtc/`  
`/remote/control_command/language/`  
`/remote/control_command/country/`  
`/remote/control_command/als_calibrate/off/`  
`/remote/control_command_post/connect_to_new_wifi/`  
`/remote/control_command_post/connect_to_exist_wifi/`  
`/remote/control_command_post/connect_to_hidden_wifi/`  
`/remote/control_command_post/delete_wifi_connection/`  
`/remote/postcard/`  

If possible, the integration prefers using the local calls instead of the Meural API. However, some settings are only available via the Meural API. This includes functionality such as pausing (changing image duration) or setting shuffle.

## Google Assistant
Meural currently only supports Alexa voice commands for the Canvas. However, if your Home Assistant supports Google Home / Google Assistant - either [configured manually](https://www.home-assistant.io/integrations/google_assistant/) or via [Nabu Casa](https://www.nabucasa.com/config/google_assistant/) - you can expose a Canvas entity and control it via Google. A media player in Home Assistant currently supports OnOff (turning the entity on or off) and Modes (changing the entity's input source) in Google. This means you can turn the Canvas on or off and select different playlists for the Canvas to display. Change the name of your Canvas to something you can pronounce - if you want to call your Canvas 'Meural', spell it 'Mural'.  

For example, say:  
*"Hey Google, turn on (canvas name)."*  
*"Hey Google, set input source to (playlist name) on (canvas name)."*  
*"Hey Google, turn off (canvas name)."*  

An example video can be found here:  
https://twitter.com/GuySie/status/1265349696119283716

For other currently missing functionality, such as next/previous track, you can create scripts in Home Assistant that can be exposed to Google that trigger the corresponding calls. E.g. write a script using the built-in editor such as:

```
'Go to next image on Meural Canvas':
  alias: next art
  sequence:
  - data: {}
    entity_id: media_player.meural-123
    service: media_player.media_next_track
```

Which would work by saying:  
*"Hey Google, activate next art."*  

## Thanks
The first version of this integration was built by [@balloob](https://github.com/balloob) - many, many thanks to him. Blame [@guysie](https://github.com/guysie) for the code added afterwards. Thanks to [@thomasvs](https://github.com/thomasvs) for contributing!
