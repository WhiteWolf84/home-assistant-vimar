[![HACS Validate](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/validate.yml/badge.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/validate.yml)
[![hassfest Validate](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/hassfest.yml/badge.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/hassfest.yml)
[![Github Release](https://img.shields.io/github/release/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/releases)
[![Github Commit since](https://img.shields.io/github/commits-since/WhiteWolf84/home-assistant-vimar/latest?sort=semver)](https://github.com/WhiteWolf84/home-assistant-vimar/releases)
[![Github Open Issues](https://img.shields.io/github/issues/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/issues)
[![Github Open Pull Requests](https://img.shields.io/github/issues-pr/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/pulls)

# VIMAR By-Me / By-Web Integration for Home Assistant

> **Current Version:** 2026.2.0  
> **Quality Level:** 🥉 Bronze (Working towards 🥈 Silver)

A comprehensive Home Assistant custom integration for the VIMAR By-me / By-web bus system.

<img title="Lights, climates, covers" src="https://user-images.githubusercontent.com/6115324/84840393-b091e100-b03f-11ea-84b1-c77cbeb83fb8.png" width="900">
<img title="Energy guards" src="https://user-images.githubusercontent.com/51525150/89122026-3a005400-d4c4-11ea-98cd-c4b340cfb4c2.jpg" width="600">
<img title="Audio player" src="https://user-images.githubusercontent.com/51525150/89122129-36b99800-d4c5-11ea-8089-18c2dcab0938.jpg" width="300">

## 🌟 What's New in 2026.2.0

### 🛠️ Complete Architecture Refactoring

The integration has undergone a major internal restructuring for better maintainability and performance:

**Modular Design:**
- `vimarlink` library split into focused components
- Separation of concerns: connection, queries, parsing, errors
- Enhanced testability and code reusability

**Performance Optimizations:**
- ⚡ **4x faster polling**: Optimized SQL queries reduce web server load
- 📦 Lightweight status updates: Only fetch changed values
- 🚫 Graceful error recovery: Prevents authentication storms

**Code Quality:**
- Full type hints for better IDE support
- Professional error handling
- Comprehensive inline documentation
- Ready for unit testing

See [CHANGELOG.md](CHANGELOG.md) for complete details.

### Time-Based Covers (Shutters) Tracking

Advanced position tracking engine for covers lacking native positional feedback:

- **Position Estimation:** Accurate 0-100% tracking based on travel times
- **Relay Delay Compensation:** Automatic adjustment for Vimar web server delays
- **Database Optimization:** Reduced HA database spam
- **Auto-calibration:** Re-sync with physical end-stops
- **Four Operating Modes:** `legacy`, `native`, `time_based`, `auto`

## 💻 Hardware Requirements

- **[Vimar 01945 - Web server By-me](https://www.vimar.com/de/int/catalog/obsolete/index/code/R01945)** or
- **[Vimar 01946 - Web server Light By-me](https://www.vimar.com/en/int/catalog/product/index/code/R01946)**

> **Note:** Tested with firmware versions v2.5 to v2.8. Always backup your Vimar database before firmware upgrades.

## 📦 Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → **Custom Repositories**
3. Add: `https://github.com/WhiteWolf84/home-assistant-vimar`
4. Category: **Integration**
5. Install and restart Home Assistant

### Manual Installation

1. Download the [latest release](https://github.com/WhiteWolf84/home-assistant-vimar/releases)
2. Extract and copy `custom_components/vimar` to your HA `custom_components` directory
3. Restart Home Assistant

## ⚙️ Configuration

Configuration is fully managed via the Home Assistant UI.

### Initial Setup

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **Vimar By-Me Hub**
4. Enter your web server credentials:
   - **Host:** IP address or hostname
   - **Port:** Usually `443` (HTTPS) or `80` (HTTP)
   - **Username:** Web server admin username
   - **Password:** Web server password
   - **SSL Certificate:** (Optional) Path to custom CA certificate

### Cover Travel Times Setup

For accurate position tracking, configure travel times per cover:

1. Go to **Developer Tools** → **Services**
2. Select `vimar.set_travel_times`
3. Choose your cover entity
4. Enter precise times:
   - `travel_time_up`: Seconds from 0% to 100%
   - `travel_time_down`: Seconds from 100% to 0%

**How to measure:**
1. Fully close the cover (0%)
2. Start timer and open to 100%
3. Record time as `travel_time_up`
4. Repeat in reverse for `travel_time_down`

## 🎯 Supported Devices

| Platform | Device Types | Status |
|----------|-------------|--------|
| **Light** | On/Off lights, Dimmers, RGB, White, Hue | ✅ Full Support |
| **Cover** | Shutters, Blinds, with/without position | ✅ Full Support |
| **Switch** | Generic switches, Outlets, Fans | ✅ Full Support |
| **Climate** | HVAC, Fancoils, Thermostats | ✅ Full Support |
| **Sensor** | Power meters, Energy guards, Temperature | ✅ Full Support |
| **Media Player** | Audio zones | ✅ Full Support |
| **Scene** | Vimar scenes | ✅ Full Support |
| **Binary Sensor** | Connection status | ✅ Full Support |

## 🛤️ Architecture

### Modular Structure

```
custom_components/vimar/
├── vimarlink/              # Core library
│   ├── __init__.py        # Package exports
│   ├── connection.py      # HTTP & authentication
│   ├── device_queries.py  # SQL query builders
│   ├── exceptions.py      # Error classes
│   ├── http_adapter.py    # SSL/TLS legacy support
│   ├── sql_parser.py      # Response parser
│   └── vimarlink.py       # Main API
├── light.py               # Light platform
├── cover.py               # Cover platform
├── climate.py             # Climate platform
├── sensor.py              # Sensor platform
└── ...                    # Other platforms
```

### Data Flow

```
HA → Coordinator → VimarLink → VimarConnection → Web Server
                   │
                   └─ VimarProject (Device Registry)
```

## 🐛 Troubleshooting

### Enable Debug Logging

Add to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.vimar: debug
    custom_components.vimar.vimarlink: debug
```

### Common Issues

#### SSL/Certificate Errors

**Problem:** `SSL: CERTIFICATE_VERIFY_FAILED` or similar SSL errors

**Solutions:**
1. **Use custom certificate:** Configure the certificate path in integration settings
2. **Update certificate:** The integration auto-downloads certificates on first connection
3. **Disable SSL verification:** (Not recommended) Use HTTP instead of HTTPS

#### Connection Timeout

**Problem:** Web server doesn't respond or times out

**Solutions:**
1. Check network connectivity to the Vimar web server
2. Verify firewall rules allow HA to reach the web server
3. Increase timeout in integration options (default: 6s)
4. Check web server load - avoid multiple simultaneous connections

#### Session Conflicts

**Problem:** Web server GUI becomes unresponsive when HA is connected

**Solution:** Create a **dedicated user** on the Vimar web server specifically for Home Assistant. The server drops sessions if the same user logs in from multiple clients.

#### Cover Position Drift

**Problem:** Cover position becomes inaccurate over time

**Solutions:**
1. Recalibrate travel times (measure multiple times and use average)
2. Perform a full open/close cycle to re-sync with end-stops
3. Switch to `auto` mode to use hardware sensors when available

## 🏆 Quality Roadmap to Silver

### Current Status: 🥉 Bronze

**Completed:**
- ✅ Stable core functionality
- ✅ Config flow (UI configuration)
- ✅ Device registry integration
- ✅ Graceful error recovery
- ✅ Code refactoring and optimization

### 🎯 Silver Requirements

**In Progress:**
- 🔄 Re-authentication flow (when credentials change/expire)
- 🔄 Enhanced documentation (troubleshooting, FAQs)
- 🔄 Proper unavailable state handling

**Planned:**
- 📝 Comprehensive entity documentation
- 📝 Integration quality scale compliance
- 🧪 Unit test suite
- ⚡ Exponential backoff retry logic
- 📦 Reduced log verbosity

**Track Progress:** [GitHub Project Board](https://github.com/WhiteWolf84/home-assistant-vimar/projects)

## ⚠️ Limitations & Disclaimer

**Supported:** Lights, dimmers, audio devices, energy meters, covers, fans, switches, climates, scenes  
**Limited Support:** Specialized hardware types may require additional development

**DISCLAIMER: THIS IS A COMMUNITY-DRIVEN PROJECT.**

Use at your own risk. This integration mimics HTTP calls made through the official Vimar By-me web interface. While extensively tested, it is not officially supported by Vimar.

## 🤝 Contributing

Contributions are welcome! Whether you:

- 🐛 Found a bug
- ✨ Have a feature request  
- 📝 Want to improve documentation
- 🔧 Can contribute code

**Steps:**
1. Check [existing issues](https://github.com/WhiteWolf84/home-assistant-vimar/issues)
2. Open a new issue or discussion
3. Fork the repo and create a feature branch
4. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Credits

**Original Authors:**
- [@h4de5](https://github.com/h4de5)
- [@robigan](https://github.com/robigan)
- [@davideciarmiello](https://github.com/davideciarmiello)

**Current Maintainer:**
- [@WhiteWolf84](https://github.com/WhiteWolf84)

**Community Contributors:**
Thank you to everyone who has contributed code, reported bugs, or helped with testing!

---

**Star this repo if you find it useful! ⭐**
