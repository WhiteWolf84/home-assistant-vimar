[![HACS Validate](https://github.com/h4de5/home-assistant-vimar/actions/workflows/validate.yml/badge.svg)](https://github.com/h4de5/home-assistant-vimar/actions/workflows/validate.yml)
[![hassfest Validate](https://github.com/h4de5/home-assistant-vimar/actions/workflows/hassfest.yml/badge.svg)](https://github.com/h4de5/home-assistant-vimar/actions/workflows/hassfest.yml)
[![Github Release](https://img.shields.io/github/release/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/releases)
[![Github Commit since](https://img.shields.io/github/commits-since/h4de5/home-assistant-vimar/latest?sort=semver)](https://github.com/h4de5/home-assistant-vimar/releases)
[![Github Open Issues](https://img.shields.io/github/issues/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/issues)
[![Github Open Pull Requests](https://img.shields.io/github/issues-pr/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/pulls)

# VIMAR By-Me / By-Web Integration for Home Assistant

A comprehensive Home Assistant custom integration for the VIMAR By-me / By-web bus system.

<img title="Lights, climates, covers" src="https://user-images.githubusercontent.com/6115324/84840393-b091e100-b03f-11ea-84b1-c77cbeb83fb8.png" width="900">
<img title="Energy guards" src="https://user-images.githubusercontent.com/51525150/89122026-3a005400-d4c4-11ea-98cd-c4b340cfb4c2.jpg" width="600">
<img title="Audio player" src="https://user-images.githubusercontent.com/51525150/89122129-36b99800-d4c5-11ea-8089-18c2dcab0938.jpg" width="300">

## 🌟 New Feature: Time-Based Covers (Shutters) Tracking

This branch (`timed-shutters-legacy-mode`) introduces an advanced position tracking engine for Vimar covers/shutters that lack native positional feedback.

**Key features of the new engine:**
- **Position Estimation:** Accurately estimates cover position (0-100%) based on configured up/down travel times.
- **Relay Delay Compensation:** Automatically compensates for the Vimar web server HTTP/Relay delay (0.5s) to provide better tracking accuracy.
- **Database Optimization:** Reduces HA database spam by throttling UI updates, avoiding interface flickering and saving I/O operations.
- **Auto-calibration:** Re-synchronizes position with physical end-stops when a full motion (0% or 100%) is executed via UI or physical buttons.
- **Four Operating Modes:**
  - `legacy`: (Default) Original behavior. Does not track time, relies only on native Vimar sensors (if present).
  - `native`: Strictly uses Vimar positional sensors. Falls back to "assumed state" if unavailable.
  - `time_based`: Forces the time-based calculation engine, ignoring Vimar positional sensors.
  - `auto`: Uses native sensors if available; automatically falls back to time-based tracking if no hardware sensor is detected.

## Hardware Requirements

- **[Vimar 01945 - Web server By-me](https://www.vimar.com/de/int/catalog/obsolete/index/code/R01945)** or
- **[Vimar 01946 - Web server Light By-me](https://www.vimar.com/en/int/catalog/product/index/code/R01946)**

> **Note:** Tested with firmware versions v2.5 to v2.8. Ensure you have a full backup of your Vimar database before performing web server firmware upgrades.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant.
2. Add this repository as a Custom Repository (Integration category).
3. Install the integration and restart Home Assistant.

### Manual Installation

Download the latest release and copy the `custom_components/vimar` folder into your Home Assistant `custom_components` directory.

## Configuration

Configuration is fully managed via the Home Assistant UI.

1. Go to **Settings > Devices & Services**.
2. Click **Add Integration** and search for **Vimar By-Me Hub**.
3. Enter your web server credentials (IP, username, password).

### Cover Travel Times Setup

Once added, you can configure individual travel times for your covers to ensure accurate tracking:

1. Go to **Developer Tools > Services**.
2. Select the `vimar.set_travel_times` service.
3. Target the specific cover entity.
4. Input the precise `travel_time_up` and `travel_time_down` (in seconds).

## Limitations & Disclaimer

This integration supports reading and controlling lights, dimmers, audio devices, energy meters, covers, fans, switches, climates, and scenes. Other specific hardware types may not be fully supported.

**DISCLAIMER: THIS IS A COMMUNITY-DRIVEN PROJECT.** Use at your own risk. It mimics HTTP calls usually made through the official Vimar By-me web interface.

## Troubleshooting

If you experience issues, enable debug logging by adding the following to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.vimar: debug
```

If the Vimar Web server GUI becomes unresponsive while Home Assistant is connected, please create a **dedicated user** on your Vimar web server specifically for this integration, as the server tends to drop sessions if the same user logs in from multiple clients.

## Contributing

Contributions, bug reports, and pull requests are always welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for more details.
