[![HACS Validate](https://github.com/h4de5/home-assistant-vimar/actions/workflows/validate.yml/badge.svg)](https://github.com/h4de5/home-assistant-vimar/actions/workflows/validate.yml)
[![hassfest Validate](https://github.com/h4de5/home-assistant-vimar/actions/workflows/hassfest.yml/badge.svg)](https://github.com/h4de5/home-assistant-vimar/actions/workflows/hassfest.yml)
[![Github Release](https://img.shields.io/github/release/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/releases)
[![Github Commit since](https://img.shields.io/github/commits-since/h4de5/home-assistant-vimar/latest?sort=semver)](https://github.com/h4de5/home-assistant-vimar/releases)
[![Github Open Issues](https://img.shields.io/github/issues/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/issues)
[![Github Open Pull Requests](https://img.shields.io/github/issues-pr/h4de5/home-assistant-vimar.svg)](https://github.com/h4de5/home-assistant-vimar/pulls)

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
3. Add: `https://github.com/h4de5/home-assistant-vimar`
4. Category: **Integration**
5. Install and restart Home Assistant

### Manual Installation

1. Download the [latest release](https://github.com/h4de5/home-assistant-vimar/releases)
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

### Modular Structure (v2026.2.0)

```
custom_components/vimar/
├── vimarlink/              # Core library
│   ├── connection.py      # HTTP & authentication
│   ├── device_queries.py  # SQL query builders
│   ├── exceptions.py      # Error classes
│   ├── http_adapter.py    # SSL/TLS legacy support
│   ├── sql_parser.py      # Response parser
│   └── vimarlink.py       # Main API
├── light.py               # Light platform
├── cover.py               # Cover platform
└── ...                    # Other platforms
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

**Problem:** `SSL: CERTIFICATE_VERIFY_FAILED`

**Solutions:**
1. Configure certificate path in integration settings
2. Integration auto-downloads certificates on first connection
3. Use HTTP instead of HTTPS (not recommended)

#### Connection Timeout

**Problem:** Web server doesn't respond

**Solutions:**
1. Check network connectivity
2. Verify firewall rules
3. Increase timeout in integration options
4. Check web server load

#### Session Conflicts

**Problem:** Web GUI becomes unresponsive when HA is connected

**Solution:** Create a **dedicated user** on the Vimar web server for Home Assistant.

#### Cover Position Drift

**Problem:** Cover position becomes inaccurate

**Solutions:**
1. Recalibrate travel times
2. Perform full open/close cycle to re-sync
3. Use `auto` mode for hardware sensors

## 🏆 Quality Roadmap to Silver

### Current: 🥉 Bronze

**Completed:**
- ✅ Stable core functionality
- ✅ Config flow (UI configuration)
- ✅ Device registry integration
- ✅ Graceful error recovery
- ✅ Modular architecture

### Target: 🥈 Silver

**In Progress:**
- 🔄 Re-authentication flow
- 🔄 Enhanced documentation
- 🔄 Proper unavailable state handling

**Planned:**
- 📝 Comprehensive troubleshooting guide
- 🧪 Unit test suite
- ⚡ Exponential backoff retry
- 📦 Reduced log verbosity

## ⚠️ Disclaimer

**THIS IS A COMMUNITY-DRIVEN PROJECT.**

Use at your own risk. This integration mimics HTTP calls made through the official Vimar By-me web interface. While extensively tested, it is not officially supported by Vimar.

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- 🐛 Report bugs via [Issues](https://github.com/h4de5/home-assistant-vimar/issues)
- ✨ Request features
- 🔧 Submit pull requests

## 📜 License

MIT License - see [LICENSE](LICENSE) file

## 🙏 Credits

**Maintainers:**
- [@h4de5](https://github.com/h4de5)
- [@robigan](https://github.com/robigan)  
- [@davideciarmiello](https://github.com/davideciarmiello)

**Contributors:**
- [@WhiteWolf84](https://github.com/WhiteWolf84) - Architecture refactoring, performance optimizations
- And all community members who reported issues and tested features!

---

**Star this repo if you find it useful! ⭐**
