# HA-meural
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs) ![Release badge](https://img.shields.io/github/v/release/guysie/ha-meural?style=for-the-badge) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT) 

**Integration for Meural Canvas digital art frame in Home Assistant**  

The [NETGEAR Meural Canvas](https://www.netgear.com/home/digital-art-canvas/) is a digital art frame with both a local interface and a cloud API.  

[Home Assistant](https://www.home-assistant.io/) is an open source home automation platform that puts local control and privacy first.  

This integration leverages Meural's API and local interface to control the Meural Canvas as a media player in Home Assistant.  

![Meural Canvas in Media Control card](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/mediacontrolcard.png)

## Installation
### HACS Install
Go to the HACS interface. Search for `HA-meural` to find this repository, select it and install.  

Restart Home Assistant after installation.

### Manual Install
Copy the `meural` folder inside `custom_components` to your Home Assistant's `custom_components` folder.  

Restart Home Assistant after copying.  

### Setup
After restarting go to *Settings*, *Devices & Services*, *Integrations*, and click *+ Add Integration* in the bottom right to add a new integration and find the Meural integration to set up.  

Choose a NETGEAR sign-in method:

- **Sign in normally from Home Assistant** when the Home Assistant connection is not blocked by NETGEAR.
- **Sign in on a phone to bypass a WAF/IP block** when interactive login requests from the Home Assistant public IP are blocked.

If NETGEAR sends a one-time verification code by email, SMS, or an authenticator app, enter it in the Home Assistant challenge screen for a normal login or in the phone browser for a mobile login. Background token refresh uses the resulting Meural refresh token and does not repeat the password login.

#### Mobile workaround for a NETGEAR WAF/IP block

1. Open Home Assistant through a secure HTTPS address that works on your phone. This may be an internal address reachable through your home Wi-Fi or VPN; Home Assistant does not need to be publicly exposed.
2. Start Meural setup or reauthentication and choose **Sign in on a phone to bypass a WAF/IP block**.
3. Open the one-time link on the phone and keep that browser tab open.
4. Turn off Wi-Fi or disconnect the VPN before entering the NETGEAR account details, so authentication uses mobile data.
5. If the page cannot send the result back, reconnect to your home Wi-Fi or VPN and press **Send to Home Assistant** in the same browser tab.
6. Return to Home Assistant to complete setup. If its window still shows the external website step because it was offline when the notification arrived, return to the mobile page and press **Refresh Home Assistant**. Reopening the same one-time link from the waiting Home Assistant window also sends this refresh notification. Beta 3 fixes refreshes for Home Assistant 2026.8 installations.

The one-time link expires after 10 minutes. The account password and verification code are sent directly from the phone browser to NETGEAR Cognito; they are not sent back to or stored by Home Assistant. The short-lived Cognito result remains only in memory in the open browser tab until delivery succeeds, and is never displayed or stored in browser storage. Repeated delivery is safe if an HTTP response gets lost. If NETGEAR also blocks the final token exchange from the home IP, stop retrying and wait for NETGEAR support to remove the block.

#### "Setup of config entry ... cancelled" after mobile sign-in

If the Home Assistant log shows `Setup of config entry '<email>' for meural integration cancelled` with a `CancelledError` right after completing mobile sign-in, the NETGEAR/Meural sign-in itself already succeeded — the cancellation happens later, while Home Assistant is connecting to the Canvas over the local network to finish setup. 

Go to *Settings* → *Devices & Services* → *Meural* and select **Reload** on the entry (or restart Home Assistant if it isn't listed yet). Since sign-in already completed, this finishes setup without repeating the mobile sign-in.

### Media Player
The integration will detect all Canvas devices registered to your account. Each Canvas will become a Media Player entity and can be added to your dashboard using any component that supports it, for example the standard Media Control card. By default your entity's name will correspond to the name of the Canvas, which out-of-the-box consists of a painter's name and 3 digits like `picasso-428` - resulting in the entity `media_player.picasso-428` being created. You can override the name and entity ID in Home Assistant's entity settings.  

It supports built-in media player service calls to pause, play, play a specific item or playlist/album, go to the next/previous track (artwork), select a source (playlist/album), set shuffle mode, and turn on or turn off.
- `media_player.media_pause`
- `media_player.media_play`
- `media_player.play_media`
- `media_player.media_next_track`
- `media_player.media_previous_track`
- `media_player.select_source`
- `media_player.shuffle_set`
- `media_player.turn_on`
- `media_player.turn_off`  

Service `media_player.play_media` can be used in 3 different ways:  
1. Temporarily displays an image from a specified URL on your Canvas.  
Set parameter `media_content_type` to `image/jpg` or `image/png`, depending on your image type, and set `media_content_id` to the URL of the image you want to display. The amount of time these images will display can be set with parameter `previewDuration` using service `meural.set_device_option`. This is most suitable for use in automations when you wish to display images temporarily on the Canvas without uploading them as artwork to the Meural servers.  
2. Displays artwork hosted on the Meural servers on your Canvas.  
Set parameter `media_content_type` to `item` and set parameter `media_content_id` to the item ID of the artwork you wish to display. You will only be able to play artwork that you have permission for, i.e. that you have uploaded yourself or that your current Meural membership gives you access to. If the artwork is not in the currently selected playlist or album, the Canvas will also switch to an *'All works'* playlist that contains all individual artwork you have played in this manner.  
3. Displays a playlist/album from your Meural account on your Canvas.
Set parameter `media_content_type` to `playlist` and parameter `media_content_id` to the gallery ID of the playlist or album that you wish to display. If the playlist is already loaded on the Canvas it will be selected directly; if it is only available in the cloud (i.e. not yet pushed to the Canvas), the integration will load it onto the Canvas via the cloud API first. When typing in gallery IDs manually, please note that albums are represented by a gallery ID on your Canvas that is not the same as their album ID on the Meural servers. To find out the gallery ID on your canvas, browse to `http://YOUR-CANVAS-IP/remote/get_galleries_json/` and locate the `id` tag next to the album or playlist name to use in the service call.

![Meural Canvas in entity settings](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/entitysettings.png)

### Backlight Light
A **Light** entity is created for each Canvas to control the backlight brightness. This gives you a familiar light-style interface with a brightness slider, and also allows turning the Canvas on and off from the Lights dashboard or light automations.

- **Turn on**: Wakes the Canvas device and optionally sets a specific brightness level.
- **Turn off**: Suspends the Canvas device (same as `media_player.turn_off`).
- **Brightness**: Sets the backlight level from 0–100%. Note that setting brightness via the `meural.set_brightness` service or the media player card simultaneously keeps both entities in sync.

The backlight entity stays in sync with the media player entity — both reflect the same sleep/wake state.

Open the light entity's detail dialog to use its brightness slider. Some Home Assistant dashboard rows show only the on/off button until the entity is opened; a Tile card with the brightness feature can keep the slider visible on a dashboard.

### Automatic Brightness
An **Auto Brightness** switch is created for each Canvas that reports support for the ambient light setting. Turning it on enables Meural's own ambient-light adjustment (`alsEnabled`); turning it off returns brightness control to the backlight slider. The switch uses the Meural cloud setting and replaces the need to call `meural.set_device_option` manually for this option.

### Canvas Settings
Supported Canvas settings are exposed as native Home Assistant entities instead of requiring `meural.set_device_option` service calls:

- **Display Orientation** select — portrait or landscape, controlled through the local Canvas API.
- **Orientation Match** switch — only show artwork matching the physical frame orientation.
- **Sleep When Dark** switch — automatically sleep and wake with the room lighting.
- **Light Sensitivity** number — ambient light sensor sensitivity from 0–100%.
- **Artwork Duration** number — seconds between artwork changes; `0` pauses rotation.
- **Image Fit Mode** select — contain, auto crop, as is, or stretch.
- **Letterbox Color** select — black, grey, or white background around unfilled artwork.

Cloud-backed setting entities are created only when the Canvas reports support for the corresponding setting.

### Local connection stability

The Canvas runs a small embedded web server that can occasionally reset or disconnect a request. The integration retries interrupted read-only requests once, uses a fresh HTTP connection for every local request, and keeps the last known state during a temporary interruption. If three consecutive updates fail, local entities become unavailable until communication recovers. The local client automatically follows IP address changes reported by the Meural cloud after DHCP lease renewals.

Do not configure the same Canvas simultaneously in this integration and the separate **Meural Canvas (Local)** integration unless you specifically need both. Each integration polls the Canvas independently, which doubles the requests to its limited local web server and can increase connection resets.

### Sensors
Five sensor entities are created for each Canvas:

- **Ambient Light** — Illuminance in lux from the local device API. Useful for automations that respond to room lighting conditions. Updates every 10 seconds, including while the Canvas is sleeping.
- **Physical Orientation** — Portrait or landscape orientation reported by the Canvas accelerometer.
- **Free Space** — Available Canvas storage in megabytes from the local device API. Diagnostic; disabled by default.
- **WiFi Signal** — WiFi signal strength in dBm from the local device API. Diagnostic; disabled by default.
- **Last Seen by Cloud** — Timestamp of the last time the device contacted the Meural cloud, from the cloud API. Useful for connectivity monitoring. Diagnostic; disabled by default.

To enable a disabled diagnostic sensor, go to *Settings* → *Devices & Services* → *Meural* → select the Canvas device → click on the sensor entity → toggle "Enable entity".

### Other Services
Additional services built into this integration are:
- `meural.set_device_option`
- `meural.set_brightness`
- `meural.reset_brightness`
- `meural.toggle_informationcard`
- `meural.synchronize`
- `meural.preview_image`
- `meural.play_random_playlist`
- `meural.load_playlist`

These services are fully documented in `services.yaml`.  

**Tip:** The official Meural settings for the sensitivity of the ambient light sensor reading are limited to high (100), medium (20) or low (4). But you can make it any value of sensitivity, on a scale of 0 to 100, using `meural.set_device_option` and setting parameter `alsSensitivity`. I find Meural's low value still makes the screen too bright for my room, so I keep `alsSensitivity` set to 2. You can experiment with this setting to fine-tune a perfect brightness to match your room.  

### Media Browser
Home Assistant's Media Browser is supported by this integration. This gives you two methods to change playlist/albums: you can still switch using the text-only source drop-down in the entity's settings, but now you can also visually browse your playlists and albums using the media browser button on the media control card or the entity's settings. Playlists and albums that are in your Meural account but not yet loaded onto the Canvas appear under a "Meural Playlists" section; selecting one will load it onto the Canvas automatically.  

![Playlists in media browser of Meural Canvas](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/mediabrowserplaylists.png)

### Media Source
HA-meural also supports displaying images from Home Assistant's Media Sources through the same Browser interface. If a source in Home Assistant, like the media folder of your installation, contains JPG or PNG files they can be displayed on the Canvas. Please note: this makes use of the preview functionality of the Canvas, and will only display the image temporarily. If you wish to increase the amount of time these images display you can set parameter `previewDuration` using service `meural.set_device_option`.  

The integration does *not* support offering the artwork displayed on the Canvas as a Media Source to other Home Assistant components.  

![Media browser of Meural Canvas](https://raw.githubusercontent.com/GuySie/ha-meural/master/images/mediabrowser.png)

### SD card folders
This integration supports the [use of SD card folders on your Canvas](https://kb.netgear.com/000060777/Can-I-use-a-micro-SD-card-to-show-my-own-images-or-videos-on-a-Meural-Canvas). The Canvas can display images from a maximum of 4 local folders named `meural1`, `meural2`, `meural3` and `meural4`. You will be able to switch between these folders, select them in the Media Browser, and go to next or previous images in them using the normal controls. However, no additional artwork information is made available by the Canvas for these images and the integration will be unable to display details such as artwork name or thumbnail.  

### Google Assistant
Meural currently only supports Alexa voice commands. However, if your Home Assistant supports Google Home / Google Assistant - either [configured manually](https://www.home-assistant.io/integrations/google_assistant/) or via [Nabu Casa](https://www.nabucasa.com/config/google_assistant/) - you can expose a Canvas entity and control it via Google. 
Media players in Home Assistant support OnOff, Modes, TransportControl and MediaState traits for Google Assistant. This means you can turn the Canvas on or off, select different playlist/albums for the Canvas to display, and perform basic controls like next/previous artwork, pause/play or enabling shuffle - though oddly Google does not support disabling shuffle.  
To make it easier to command your Canvas change the name to something you can pronounce and Google can recognize as a word - e.g. if you want to call your Canvas 'Meural', spell it 'Mural'.  

For example, you can say:  
*"Hey Google, turn on (canvas name)."*  
*"Hey Google, pause (canvas name)."*  
*"Hey Google, set input to (playlist/album name) on (canvas name)."*  
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
It's not elegant, but it works.

**Tip:** A lot of problems between Home Assistant and Google Assistant stem from incorrectly synced entities between the two platforms. If you're having issues, try saying the following:  
*"Hey Google, sync devices."*

## Meural Canvas device

### Meural API
Meural has a REST API that their [mobile apps](https://www.netgear.com/home/meural-digital-frame/meural-app/) and [web-interface](https://my.meural.netgear.com/) run on. Unofficial documentation on this API can be found here:
https://documenter.getpostman.com/view/1657302/RVnWjKUL

### Local Web Server
Netgear refers to a 'remote controller' in their Meural support documentation:  
https://kb.netgear.com/000060746/Can-I-control-the-Canvas-without-a-mobile-app-or-gesture-control-and-if-so-how  
This 'remote controller' is a local web server on the Canvas device available at: `http://YOUR-CANVAS-IP/remote/`  
It runs on a javascript available at: `http://YOUR-CANVAS-IP/static/remote.js`

The available calls in this javascript are:
- `/remote/identify/`
- `/remote/get_galleries_json/`
- `/remote/get_gallery_status_json/`
- `/remote/get_frame_items_by_gallery_json/`
- `/remote/get_wifi_connections_json/`
- `/remote/get_backlight/`
- `/remote/control_check/sleep/`
- `/remote/control_check/video/`
- `/remote/control_check/als/`
- `/remote/control_check/system/`
- `/remote/control_command/boot_status/image/`
- `/remote/control_command/set_key/`
- `/remote/control_command/set_backlight/`
- `/remote/control_command/suspend`
- `/remote/control_command/resume`
- `/remote/control_command/set_orientation/`
- `/remote/control_command/change_gallery/`
- `/remote/control_command/change_item/`
- `/remote/control_command/rtc/`
- `/remote/control_command/language/`
- `/remote/control_command/country/`
- `/remote/control_command/als_calibrate/off/`
- `/remote/control_command_post/connect_to_new_wifi/`
- `/remote/control_command_post/connect_to_exist_wifi/`
- `/remote/control_command_post/connect_to_hidden_wifi/`
- `/remote/control_command_post/delete_wifi_connection/`
- `/remote/postcard/`  

## AI
Since v2.0.0, this integration is being maintained with help from Claude Code. If you are against using AI-generated code, please stay on a v1.x version or fork from that point to pursue your own development.
