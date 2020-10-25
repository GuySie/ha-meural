# HA-meural
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs) ![Release badge](https://img.shields.io/github/v/release/guysie/ha-meural?style=for-the-badge) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT) 

**Integration for Meural Canvas digital art frame in Home Assistant**  

The [NETGEAR Meural Canvas](https://meural.netgear.com/) is a digital art frame with both a local interface and a cloud API.  
[Home Assistant](https://www.home-assistant.io/) is an open source home automation package that puts local control and privacy first.  
This integration leverages Meural's API and local interface to control the Meural Canvas as a media player in Home Assistant.  

![Meural Canvas in Media Control card](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/mediacontrolcard.png)

## Installation
### HACS Install
Go to HACS (Community). Select *Integrations* and click the + to add a new integration repository. Search for `HA-meural` to find this repository, select it and install.  
Restart Home Assistant after installation.

### Manual Install
Copy the `meural` folder inside `custom_components` to your Home Assistant's `custom_components` folder.  
Restart Home Assistant after copying.  

### Setup
After restarting go to *Configuration*, *Integrations*, click the + to add a new integration and find the Meural integration to set up.  

Log in with your NETGEAR account.  

The integration will detect all Canvas devices registered to your account. Each Canvas will become a Media Player entity and can be added to your Lovelace UI using any component that supports it, for example the Media Control card. By default your entity's name will correspond to the name of the Canvas, which out-of-the-box consists of a painter's name and 3 digits like `picasso-428` - resulting in the entity `media_player.picasso-428` being created. You can override the name and entity ID in Home Assistant's entity settings.  

## Integration

The integration supports built-in media player service calls to pause, play, play a specific item or playlist, go to the next/previous track (artwork), select a source (art playlist/album), set shuffle mode, and turn on or turn off. It also supports the built-in Media Browser functionality.  
`media_player.media_pause`  
`media_player.media_play`  
`media_player.play_media`  
`media_player.media_next_track`  
`media_player.media_previous_track`  
`media_player.select_source`  
`media_player.shuffle_set`  
`media_player.turn_on`  
`media_player.turn_off`  

Service `media_player.play_media` can be used in 3 different ways:  
1. Temporarily displays an image from a specified URL on your Canvas.  
Set parameter `media_content_type` to `image/jpg` or `image/png`, depending on your image type, and set `media_content_id` to the URL of the image you want to display. The amount of time these images will display can be set with parameter `previewDuration` using service `meural.set_device_option`. This is most suitable for use in automations when you wish to display images temporarily on the Canvas without uploading them as artwork to the Meural servers.  
2. Displays artwork hosted on the Meural servers on your Canvas.  
Set parameter `media_content_type` to `item` and set parameter `media_content_id` to the item ID of the artwork you wish to display. You will only be able to play items that you have permission for, i.e. artwork you have uploaded yourself or that your Meural membership gives you access to. If the item is not in the currently selected playlist, the Canvas will also switch to an *'All works'* playlist that contains all items you have played in this manner.  
3. Displays a playlist/album that is already uploaded to your Canvas.  
Set parameter `media_content_type` to `playlist` and parameter `media_content_id` to the gallery ID of the playlist or album that you wish to display. You will not be able to display a playlist or album that has not yet been sent to the Canvas. Please note that albums are assigned a 'fake' gallery ID on your Canvas to represent them that is not the same as their album ID on the Meural servers.  

![Meural Canvas in entity settings](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/entitysettings.png)

Additional services built into this integration are:  
`meural.set_device_option`  
`meural.set_brightness`  
`meural.reset_brightness`  
`meural.toggle_informationcard`  
`meural.synchronize`  
`meural.preview_image`  
These services are fully documented in `services.yaml`.  

**Tip:** The official Meural settings for the sensitivity of brightness to ambient light sensor reading are limited to high (100), medium (20) or low (4). But you can make it any value of sensitivity, on a scale of 0 to 100, using `meural.set_device_option` and setting parameter `alsSensitivity`. I find Meural's low value still makes the screen too bright for my room, so I keep `alsSensitivity` set to 2.  

### Google Assistant
Meural currently only supports Alexa voice commands for the Canvas. However, if your Home Assistant supports Google Home / Google Assistant - either [configured manually](https://www.home-assistant.io/integrations/google_assistant/) or via [Nabu Casa](https://www.nabucasa.com/config/google_assistant/) - you can expose a Canvas entity and control it via Google. 
Starting from [Home Assistant release 111](https://www.home-assistant.io/blog/2020/06/10/release-111/) a media player supports OnOff, Modes, TransportControl and MediaState in Google. This means you can turn the Canvas on or off, select different playlists for the Canvas to display, and perform basic controls like next/previous image, pause/play or enabling shuffle - oddly enough, Google does not support disabling shuffle.  
To make it easier to command your Canvas change the name to something you can pronounce and Google can recognize - e.g. if you want to call your Canvas 'Meural', spell it 'Mural'.  

For example, you can say:  
*"Hey Google, turn on (canvas name)."*  
*"Hey Google, pause (canvas name)."*  
*"Hey Google, set input to (playlist name) on (canvas name)."*  
*"Hey Google, set (canvas name) to shuffle."*  
*"Hey Google, next image on (canvas name)."*  
*"Hey Google, play (canvas name)."*  
*"Hey Google, turn off (canvas name)."*  

For other currently missing functionality, such as turning shuffle off, you can create scripts in Home Assistant that can be exposed to Google to trigger the corresponding services. These scripts are called by saying *"Hey Google, activate (script name)."*  
Then write a script using the built-in editor such as:

```
'Disable shuffle on Meural Canvas':
  alias: Disable art shuffle
  sequence:
  - data:
      shuffle: false
    entity_id: media_player.meural-123
    service: media_player.shuffle_set
```

Which would work by saying:  
*"Hey Google, activate disable art shuffle."*  

**Tip:** A lot of problems between Home Assistant and Google Assistant stem from incorrectly synced entities between the two platforms. If you're having issues, try saying the following:  
*"Hey Google, sync devices."*

## Meural Canvas device

### Meural API
Meural has a REST API that their [mobile apps](https://www.netgear.com/support/product/mc327.aspx#download) and [web-interface](https://my.meural.netgear.com/) run on. Unofficial documentation on this API can be found here:
https://documenter.getpostman.com/view/1657302/RVnWjKUL?version=latest

### Local Web Server
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

## Thanks
The first version of this integration was built by [@balloob](https://github.com/balloob) - many, many thanks to him. Blame [@guysie](https://github.com/guysie) for the code added afterwards. Thanks to [@thomasvs](https://github.com/thomasvs) for contributing the code to preview images!