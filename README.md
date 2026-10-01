# Shelly Integration for Unfolded Circle Remote 2/3

Control your **Shelly smart-home devices** directly from your Unfolded Circle Remote 2 or Remote 3. Relays, plugs, dimmers, RGB/RGBW/CCT lights, roller shutters, thermostats, energy meters and sensors - all over your local network, with realtime push updates for Gen2+ devices.

![Shelly](https://img.shields.io/badge/Shelly-Local%20API-4CAF50)
[![GitHub Release](https://img.shields.io/github/v/release/mase1981/uc-intg-shelly?style=flat-square)](https://github.com/mase1981/uc-intg-shelly/releases)
![License](https://img.shields.io/badge/license-MPL--2.0-blue?style=flat-square)
[![GitHub issues](https://img.shields.io/github/issues/mase1981/uc-intg-shelly?style=flat-square)](https://github.com/mase1981/uc-intg-shelly/issues)
[![Community Forum](https://img.shields.io/badge/community-forum-blue?style=flat-square)](https://unfolded.community/)
[![Discord](https://badgen.net/discord/online-members/zGVYf58)](https://discord.gg/zGVYf58)
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/mase1981/uc-intg-shelly/total?style=flat-square)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=flat-square)](https://buymeacoffee.com/meirmiyara)
[![PayPal](https://img.shields.io/badge/PayPal-donate-blue.svg?style=flat-square)](https://paypal.me/mmiyara)
[![Github Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-30363D?&logo=GitHub-Sponsors&logoColor=EA4AAA&style=flat-square)](https://github.com/sponsors/mase1981)

## Supported Devices

All mains-powered Shelly devices on your local network:

- **Gen1** - Shelly 1 / 1PM / 1L / 2.5 / Plug / Plug S / Dimmer / Dimmer 2 / RGBW2 / Bulb / Duo / Vintage / EM / 3EM / i3 / Uni and more (HTTP API).
- **Gen2 (Plus / Pro)**, **Gen3**, **Gen4** - Plus 1 / 1PM / 2PM / Plug / i4 / RGBW PM / Wall Dimmer, Pro 1-4 / Pro Dual Cover / Pro EM / Pro 3EM / Pro Dimmer, Mini series, Gen3/Gen4 switches, dimmers, plugs and meters, BLU Gateway (BLU TRV and BTHome sensors), Shelly Wall Display, and more (WebSocket RPC API).

## Features

- **💡 Lights** - dimmers, bulbs, RGB / RGBW / CCT / RGBCCT controllers, and relays whose output type is set to *light*: on/off, brightness, colour and colour temperature.
- **🔌 Switches** - relays, plugs, Pro circuit breakers, virtual boolean components and scripts (start/stop).
- **🪟 Covers** - roller shutters and blinds with position and slat tilt; live position while moving.
- **🌡️ Climate** - Wall Display thermostat, BLU TRV radiator valves (via BLU Gateway) and Gen1 TRV.
- **📊 Sensors** - power, energy, returned energy, voltage, current, frequency, power factor, per-phase EM/3EM data, temperature, humidity, illuminance, battery, analog/counter inputs, Wi-Fi signal, uptime, BTHome sensors and virtual number/text components.
- **🚨 Binary sensors** - door/window, motion, flood, smoke, gas, vibration, presence, inputs, overheating/overpower/overvoltage/overcurrent, cloud connection, restart required and firmware update available.
- **🔘 Buttons** - reboot, firmware update, virtual buttons, cover/TRV calibration, smoke alarm mute.
- **📋 Selects** - virtual enum components and Gen1 RGB light effects.
- **🎵 Media player** - Shelly Wall Display (XL) playback, volume, artwork, and browsing of radio favourites and audio files.
- **📡 IR emitter** - devices exposing `IR.EmitRaw` can send PRONTO codes from the Remote (experimental).
- **🎮 Remote entity** - every output, cover and button as simple commands (`SWITCH_0_ON`, `COVER_0_OPEN`, `ALL_OFF` ...) with UI pages for activities and macros.
- **⚡ Realtime** - Gen2+ devices push state changes over WebSocket; Gen1 devices are polled every 5 seconds.
- **🔐 Password protection** - Gen2+ digest authentication and Gen1 basic authentication.

---
## ❤️ Support Development ❤️

If you find this integration useful, consider supporting development:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-pink?style=for-the-badge&logo=github)](https://github.com/sponsors/mase1981)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/meirmiyara)
[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/mmiyara)

Your support helps maintain this integration. Thank you! ❤️
---

## How It Works

The integration talks to each Shelly directly on your local network - no Shelly Cloud account is needed:

- **Gen2 / Gen3 / Gen4** - a WebSocket JSON-RPC connection (`ws://<device>/rpc`). The device pushes every state change instantly; a slow safety poll runs in the background and the connection is re-opened automatically if it drops.
- **Gen1** - the HTTP REST API, polled every 5 seconds and immediately after every command.
- **Entities are built from the device itself** - during setup the integration reads the device's components and creates exactly the entities that device supports.

## Requirements

- Shelly device(s) on the same network as the Remote, with a fixed IP address (DHCP reservation) recommended.
- If the device is password protected: the device password (and username for Gen1, default `admin`).
- Unfolded Circle Remote 2 / 3 with firmware supporting custom integrations.

## Installation

### Option 1: Remote Web Interface (Recommended)

1. Download the latest `uc-intg-shelly-<version>-aarch64.tar.gz` from the [**Releases**](https://github.com/mase1981/uc-intg-shelly/releases) page.
2. Open your Remote's web interface (`http://your-remote-ip`).
3. Go to **Settings → Integrations → Add Integration → Install Custom** and upload the `.tar.gz`.

### Option 2: Docker (Advanced Users)

**Image**: `ghcr.io/mase1981/uc-intg-shelly:latest`

**Docker Compose:**
```yaml
services:
  uc-intg-shelly:
    image: ghcr.io/mase1981/uc-intg-shelly:latest
    container_name: uc-intg-shelly
    network_mode: host
    volumes:
      - ./config:/config
    environment:
      - UC_CONFIG_HOME=/config
      - UC_INTEGRATION_HTTP_PORT=9090
      - UC_INTEGRATION_INTERFACE=0.0.0.0
      - PYTHONPATH=/app
    restart: unless-stopped
```

**Docker Run:**
```bash
docker run -d --name uc-intg-shelly --restart unless-stopped --network host -v $(pwd)/config:/config -e UC_CONFIG_HOME=/config -e UC_INTEGRATION_INTERFACE=0.0.0.0 -e UC_INTEGRATION_HTTP_PORT=9090 -e PYTHONPATH=/app ghcr.io/mase1981/uc-intg-shelly:latest
```

## Configuration

1. Start setup. Leave the **IP address** blank to auto-discover Shelly devices (mDNS), or enter the IP manually.
2. If the device is password protected, enter the **password** (Gen1 devices also need the **username**, default `admin`).
3. If more than one device is found, pick it from the list.
4. Repeat the setup for each additional Shelly device.

Each device creates the entities it supports, for example:

| Entity | Purpose |
|--------|---------|
| **Switch** (`<name>`) | Relay / plug output, circuit breaker, virtual boolean, script |
| **Light** (`<name>`) | Dimmer, bulb, RGB/RGBW/CCT channel, relay set to *light* |
| **Cover** (`<name>`) | Roller shutter / blind with position and tilt |
| **Climate** (`<name>`) | Wall Display thermostat, BLU TRV, Gen1 TRV |
| **Sensors** | Power, energy, voltage, current, temperature, humidity, battery, door/window, motion, flood, smoke ... |
| **Buttons** | Reboot, firmware update, virtual buttons, calibration |
| **Select** | Virtual enum, Gen1 light effect |
| **Media Player** | Wall Display media |
| **Remote** (`<name> Remote`) | All outputs as simple commands with UI pages |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No Shelly devices were found" | Make sure the device is powered on and on the same network/VLAN as the Remote, or enter the IP address manually. |
| "Password protected or the password is wrong" | Enter the device password set in the Shelly app / web UI (Gen1: also the username). |
| Entities unavailable | The device is unreachable (power, Wi-Fi or IP change). It recovers automatically; use a DHCP reservation to keep the IP fixed. |
| New channels / components not shown | Components added on the device after setup (e.g. new virtual components or a changed relay type) are detected on connect; restart the integration (or re-run setup) to add them. |
| Battery devices rarely update | Battery-powered Shelly sensors (H&T, Door/Window, Flood, Smoke, TRV, Plus H&T) sleep most of the time and are only reachable while awake, so they are best added while awake and may show as unavailable between wake-ups. |

## Credits

- **Developer**: Meir Miyara
- **Shelly protocol**: based on the [Shelly API documentation](https://shelly-api-docs.shelly.cloud/), the [aioshelly](https://github.com/home-assistant-libs/aioshelly) library and the [Home Assistant Shelly integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/shelly).
- **Unfolded Circle**: Remote 2/3 integration framework ([ucapi](https://github.com/unfoldedcircle/integration-python-library) / [ucapi-framework](https://github.com/JackJPowell/ucapi-framework)).

## License

Mozilla Public License 2.0 (MPL-2.0) - see the LICENSE file.

## Support & Community

- **GitHub Issues**: [Report bugs and request features](https://github.com/mase1981/uc-intg-shelly/issues)
- **UC Community Forum**: [General discussion and support](https://unfolded.community/)
- **Developer**: [Meir Miyara](https://www.linkedin.com/in/meirmiyara)

---

**Made with ❤️ for the Unfolded Circle Community**

**Thank You**: Meir Miyara
