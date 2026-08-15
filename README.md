[![HACS Validate](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/validate.yml/badge.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/validate.yml)
[![hassfest Validate](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/hassfest.yml/badge.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/actions/workflows/hassfest.yml)
[![Github Release](https://img.shields.io/github/release/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/releases)
[![Github Commit since](https://img.shields.io/github/commits-since/WhiteWolf84/home-assistant-vimar/latest)](https://github.com/WhiteWolf84/home-assistant-vimar/releases)
[![Github Open Issues](https://img.shields.io/github/issues/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/issues)
[![Github Open Pull Requests](https://img.shields.io/github/issues-pr/WhiteWolf84/home-assistant-vimar.svg)](https://github.com/WhiteWolf84/home-assistant-vimar/pulls)

# VIMAR By-Me / By-Web Integration for Home Assistant

> **Current Version:** 2026.8.2 · **Requires:** Home Assistant 2026.5.0+ · **Python:** 3.14.2+ (imposed by Home Assistant 2026.3+; the standalone `vimarlink` library still runs on 3.13)

A comprehensive Home Assistant custom integration for the VIMAR By-me / By-web bus system. Controls lights, covers, climate, switches, sensors, media players, scenes, and the **SAI2 alarm system** through the VIMAR web server.

<img title="Lights, climates, covers" src="https://user-images.githubusercontent.com/6115324/84840393-b091e100-b03f-11ea-84b1-c77cbeb83fb8.png" width="900">
<img title="Energy guards" src="https://user-images.githubusercontent.com/51525150/89122026-3a005400-d4c4-11ea-98cd-c4b340cfb4c2.jpg" width="600">
<img title="Audio player" src="https://user-images.githubusercontent.com/51525150/89122129-36b99800-d4c5-11ea-8089-18c2dcab0938.jpg" width="300">

## 💻 Hardware Requirements

- **[Vimar 01945 - Web server By-me](https://www.vimar.com/de/int/catalog/obsolete/index/code/R01945)** or
- **[Vimar 01946 - Web server Light By-me](https://www.vimar.com/en/int/catalog/product/index/code/R01946)**

> **Note:** Tested with firmware versions v2.5 to v2.11. Always backup your Vimar database before firmware upgrades.

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
3. Search for **VIMAR By-Me Hub**
4. Enter your web server credentials:
   - **Host:** IP address or hostname
   - **Port:** Usually `443` (HTTPS) or `80` (HTTP)
   - **Username:** Web server admin username
   - **Password:** Web server password
   - **SSL Certificate:** (Optional) Path to custom CA certificate

### Options Flow

After initial setup, click **Configure** on the integration. The options are
split across five screens.

#### Connection

Host, port, credentials and SSL — the same fields as the initial setup.

#### Integration settings

- **Polling interval:** how often device states are read (default 8 s)
- **Energy refresh interval:** how often energy meters are explicitly refreshed. `0` disables it, and the meters then freeze on a stale value
- **Ignored platforms:** exclude platforms from discovery
- **Cover position mode:** `auto` (default), `native`, `time_based` or `legacy`
- **Use Vimar device names:** take the bus name verbatim instead of deriving one. ⚠️ Puts the room back into the device name — see [Naming](#-naming)
- **Prepend room name to device names:** ⚠️ same caveat
- **Tag devices with their Vimar room:** applies the room as a label, on by default — see [Naming](#-naming)
- **Light / binary sensor filters:** regular expressions reclassifying `CH_Main_Automation` devices

#### Advanced options

*Force reload all entities*, which deletes and re-creates every entity on the
next restart.

#### Device overrides

See [Device overrides](#-device-overrides).

#### Alarm PIN per user

Maps each Home Assistant user to their SAI2 PIN, plus a fallback for
automations; see [SAI2 Alarm System](#-sai2-alarm-system).

> ⚠️ Changing **Use Vimar device names**, **Prepend room name**, or either
> regex filter sets the *force reload* flag: every entity is deleted and
> re-created on the next restart, which loses entity IDs, area assignments and
> manual renames. Don't toggle them just to look.

## 🎯 Supported Devices

| Platform | Device Types | Status |
|----------|-------------|--------|
| **Light** | On/Off lights, Dimmers, RGB, White, Hue | ✅ Full Support |
| **Cover** | Shutters, Blinds — with native or time-based position tracking | ✅ Full Support |
| **Switch** | Generic switches, Outlets, Fans | ✅ Full Support |
| **Climate** | HVAC, Fancoils, Thermostats | ✅ Full Support |
| **Sensor** | Power meters, Energy guards, Temperature | ✅ Full Support |
| **Media Player** | Audio zones | ✅ Full Support |
| **Scene** | Vimar scenes | ✅ Full Support |
| **Alarm Control Panel** | SAI2 alarm areas — arm/disarm, multi-area, per-user PIN | ✅ Full Support |
| **Binary Sensor** | SAI2 alarm zone sensors (door contacts, motion, tamper) + connection status | ✅ Full Support |

## 🔤 Naming

Home Assistant builds what you read — and the entity ID it generates — from
three parts: **area + device + entity**. The integration therefore names each
part once and lets Home Assistant put them together.

- **Entities name their function only.** A shutter, a thermostat, a switch or a scene names nothing at all: it *is* its device, so the device name is what you see. A sensor names just the quantity it measures (`Dynamic Mode`, `Forzatura`, `Autoconsumo Totale`).
- **Devices are named after what they are, not where they are.** A bus object called `TAPPARELLA BAGNETTO` becomes the device **Tapparella** in the area **Bagnetto**. The room is subtracted using the rooms the web server itself assigns to the object, so a floor modelled as a second room goes too: `LUCE 11 CUCINA PIANO TERRA` in *Cucina / Piano Terra* becomes **Luce 11**.
- **The room is kept as a label.** The area is where *you* want a device and you can reorganise it freely; the label is what VIMAR says the room is. Labels can be targeted directly, so `label_id: bagnetto` reaches every Vimar device in that room without listing any of them. Turn it off with **Tag devices with their Vimar room**.

Objects the web server places in no room, and objects named after their room
and nothing else, keep the name they have always had.

**Renaming a device in Home Assistant always wins** — the integration has never
been able to override that.

### Recreating entity IDs

Existing entity IDs never change on their own. To take up the naming above on
entities that already exist, use **Settings → Devices & Services → Entities →
Recreate entity IDs**, and read the preview before confirming.

Do it in this order:

1. Back up `.storage/core.entity_registry` and `.storage/core.device_registry`.
2. Upgrade and restart. Check the entities read as you expect. Everything to here is reversible.
3. If you renamed entities by hand to work around the old naming, clear those names now — not before step 2, or they will briefly show both names at once.
4. Only then, *Recreate entity IDs*. Recorded history and long-term statistics follow the rename automatically; references in your automations, scripts and dashboards do not.

## 🧩 Device overrides

Overrides are rules that change how a VIMAR device is exposed: forcing it onto
a different platform, giving it a device class, setting its icon, or using the
raw VIMAR name. Manage them under **Configure → Device overrides**.

Each rule matches a device and then sets what should change:

| Field | Meaning |
|---|---|
| **Match on** | `vimar_name` (the bus object name), `vimar_object_type` (e.g. `CH_Main_Automation`), `friendly_name` or `device_type` |
| **How to match** | `exact`, `regex`, or `all` (every device) |
| **Value to match** | The name or pattern; ignored for `all` |
| **Force platform** | `switch`, `light`, `cover`, `climate`, `sensor`, `binary_sensor`, `scene`, `media_player`, … |
| **Force device class** | e.g. `garage`, `outlet`, `shutter` |
| **Icon (on / off)** | Two icons give a state-dependent pair; one icon is used for both |
| **Use the raw VIMAR name** | Names the device exactly as the bus does |

Rules apply **in order**: a later rule overwrites what an earlier one set. New
rules are appended, so the newest wins.

### Migrating from `configuration.yaml`

Overrides used to be written by hand in YAML. On the first start after
upgrading, any `device_override:` block is **imported into the integration
automatically** and a line in the log tells you it can be removed:

```yaml
# No longer read once imported — the options own these now
vimar:
  device_override:
    - filter_vimar_name: '*'
      object_name_as_vimar: true
    - filter_vimar_name: 'Garage'
      device_type: switches
      device_class: garage
      icon: mdi:garage-open,mdi:garage
```

The import happens **once**. Deleting every rule in the UI afterwards does not
bring the YAML ones back, and a `device_override:` block added to YAML later is
not picked up.

> **Beyond the form.** The override language also supports regex substitutions
> (`*_regexsub_pattern` / `_repl`), filters on arbitrary device fields and a few
> per-rule flags with no control on the form. Such a rule stays listed and
> editable — the parts the form does not know about are carried across
> untouched — but those parts can still only be written in YAML.

## 🚨 SAI2 Alarm System

Full integration with the VIMAR SAI2 domestic alarm system.

### Alarm Control Panel

Each SAI2 area is exposed as an `alarm_control_panel` entity supporting:

| Action | Description |
|--------|-------------|
| **Disarm** | Disarm the area |
| **Arm Away** | Full arming (all sensors active) |
| **Arm Home** | Internal arming (perimeter sensors only) |
| **Arm Night** | Partial arming |

**Features:**
- Multi-area support — each SAI2 group is a separate entity
- **No global PIN stored** — the code is forwarded to the SAI2 control unit as the user PIN, so each person can use their own PIN and the panel logs the operation against the right user
- **PIN validated up-front** via `service-vimarsai2authenticate`: a wrong PIN is reported immediately and clearly (the set command always acknowledges with `DPCM-0000`, even for a wrong PIN, so the response alone can't be trusted)
- **Per-user PIN mapping** — a logged-in HA user with a mapped PIN arms/disarms with a single tap (no keypad); others and automations pass the code explicitly or use the fallback PIN
- **Persistent notification** (localized) on command failures, so errors aren't easy to miss
- Automatic disarm-before-rearm when switching between armed modes
- Live state from DPADD_OBJECT bitmask polling
- All entities grouped under a single **SAI Alarm** device

### Zone Binary Sensors

Each SAI2 zone is exposed as a `binary_sensor` with automatic device class detection:

| Zone Name Keywords | Device Class |
|-----------|--------------|
| porta, ingresso, basculante | `door` |
| finestra | `window` |
| volumetrico, PIR, motion | `motion` |
| sirena, manomissione, tamper | `tamper` |

**Extra attributes:** `raw_value`, `excluded`, `alarm`, `tampered`, `masked`, `memory`, `area`

### Setup

1. Alarm entities appear automatically after integration reload — reading state needs no PIN.
2. Arming/disarming requires the SAI2 PIN (the same code used on the Vimar web interface). The integration does **not** store a single global PIN; instead, in **Configure → Alarm PIN per user**:
   - map each Home Assistant user to their own SAI2 PIN → that user arms/disarms with one tap, and
   - optionally set a **fallback PIN for automations**, used when a command has no explicit `code` and no user in context (trigger-based automations).

   You can always pass the PIN explicitly as the service `code`, e.g. `alarm_control_panel.alarm_arm_away` with `data: { code: "1234" }`.
3. Zone sensors update via slim poll (real-time parent bitmask)

## 🏠 Cover Position Tracking

Advanced position tracking for covers lacking native positional feedback.

### Operating Modes

| Mode | Description |
|------|-------------|
| **`auto`** (default) | Uses hardware sensor when available, falls back to time-based tracking |
| **`native`** | Hardware position sensors only |
| **`time_based`** | Always uses time-based calculation |
| **`legacy`** | Original master branch behavior (no tracking) |

### Travel Time Calibration

For accurate position tracking without hardware sensors:

1. Go to **Developer Tools** → **Services**
2. Select `vimar.set_travel_times`
3. Choose your cover entity
4. Enter measured times:
   - `travel_time_up`: Seconds from fully closed to fully open
   - `travel_time_down`: Seconds from fully open to fully closed

**Features:**
- 200ms internal calculation interval, UI state updated every 1% position change
- Position persistence across HA restarts
- Physical button detection (wall switches auto-sync to 0%/100%)
- Relay delay compensation for Vimar web server latency
- Per-entity travel time configuration via entity options

## 🛠️ Architecture

### Modular Structure

```
custom_components/vimar/
├── vimarlink/                    # Core library (HA-independent)
│   ├── connection.py            # HTTP & authentication
│   ├── device_queries.py        # SQL query builders
│   ├── exceptions.py            # Error classes
│   ├── http_adapter.py          # SSL/TLS legacy support
│   ├── sql_parser.py            # Response parser
│   └── vimarlink.py             # Main API facade
├── alarm_control_panel.py       # SAI2 alarm platform
├── binary_sensor.py             # Binary sensors + SAI2 zones
├── climate.py                   # HVAC / thermostats
├── config_flow.py               # UI configuration
├── const.py                     # Constants
├── cover.py                     # Covers with time-based tracking
├── light.py                     # Lights / dimmers / RGB
├── media_player.py              # Audio zones
├── override_editor.py           # Override rule <-> options form
├── scene.py                     # Scenes
├── sensor.py                    # Power / energy / temperature
├── switch.py                    # Switches / outlets
├── vimar_coordinator.py         # DataUpdateCoordinator
├── vimar_device_customizer.py   # Device type overrides
└── vimar_entity.py              # Base entity class
```

### Key Design Decisions

- **Slim polling:** After initial discovery, updates query only status IDs — ~90% less DB workload
- **Hash-based change detection:** Only devices with changed status hashes trigger HA state writes
- **Modular `vimarlink`:** Core library has zero HA dependencies, usable standalone
- **Re-authentication flow:** `ConfigEntryAuthFailed` triggers automatic reauth dialog
- **Entity availability:** Reports `unavailable` when web server is offline, auth fails, or device is removed
- **`has_entity_name` naming:** every entity names its own function and lets Home Assistant prepend area and device, so no part of the name is said twice
- **The config entry owns every setting:** device overrides were the last thing readable only from YAML; they are migrated into the entry once, and YAML is not consulted again

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
1. Check network connectivity and firewall rules
2. Increase timeout in integration options
3. Check web server load — create a dedicated HA user

#### Session Conflicts

**Problem:** Web GUI becomes unresponsive when HA is connected

**Solution:** Create a **dedicated user** on the Vimar web server for Home Assistant.

#### Cover Position Drift

**Problem:** Cover position becomes inaccurate over time

**Solutions:**
1. Recalibrate travel times with precise measurements
2. Perform full open/close cycle to auto-calibrate end-stops
3. Switch to `native` mode if hardware sensors are available

#### A Device Changed Name After Upgrading

**Problem:** devices are now called `Tapparella` or `Luce 11` instead of
`Bagnetto` or `Piano Terra Cucina 11`.

**Explanation:** this is the [naming change](#-naming) — the device is named
after what it is, and the room it sits in comes from its area. Entity IDs, unique
IDs, history and statistics are unaffected.

**Solutions:**

1. Rename the device in Home Assistant if you prefer another name; your name always wins and survives every future upgrade
2. Turn on **Use Vimar device names** to take the bus name verbatim instead — but note it puts the room back into the name, and that toggling it forces a full entity reload

#### My `device_override` Rules Stopped Working

**Problem:** rules in `configuration.yaml` no longer seem to apply.

**Explanation:** they were imported into the integration on the first start
after upgrading, and YAML is not read any more. Look for the `Imported N
device_override rule(s)` line in the log.

**Solutions:**

1. Open **Configure → Device overrides** — your rules should be listed there, in the same order
2. Edit them there from now on; a `device_override:` block added to YAML afterwards is not picked up
3. Once you have confirmed they are present, remove the block from `configuration.yaml`

#### SAI2 Alarm Not Responding

**Problem:** Alarm entities appear but commands fail

**Solutions:**
1. Verify the SAI2 PIN is correct — a wrong PIN now surfaces a clear "wrong PIN" error and notification. Set it per-user (or the automation fallback) in **Configure → Alarm PIN per user**, or pass it as the service `code`
2. Check that the Vimar web server user has SAI access permissions
3. For trigger-based automations, make sure a `code` is passed or the **fallback PIN for automations** is configured (automations run without a user, so per-user PINs don't apply)
4. Enable debug logging for `custom_components.vimar.alarm_control_panel`

## 🌍 Internationalization

Config flow, options flow, and reauth flow are fully translated in **7 languages**:

🇬🇧 English · 🇮🇹 Italian · 🇩🇪 German · 🇫🇷 French · 🇪🇸 Spanish · 🇳🇱 Dutch · 🇵🇹 Portuguese

## ⚠️ Disclaimer

**THIS IS A COMMUNITY-DRIVEN PROJECT.**

Use at your own risk. This integration mimics HTTP calls made through the official Vimar By-me web interface. While extensively tested, it is not officially supported by Vimar.

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- 🐛 Report bugs via [Issues](https://github.com/WhiteWolf84/home-assistant-vimar/issues)
- ✨ Request features
- 🔧 Submit pull requests

## 📜 License

MIT License — see [LICENSE](LICENSE) file

## 🙏 Credits

**Maintainers:**
- [@h4de5](https://github.com/h4de5)
- [@robigan](https://github.com/robigan)
- [@davideciarmiello](https://github.com/davideciarmiello)

**Contributors:**
- [@WhiteWolf84](https://github.com/WhiteWolf84) — Architecture refactoring, performance optimizations, SAI2 alarm integration (powered by [Claude Opus](https://claude.ai))
- And all community members who reported issues and tested features!

---

**Star this repo if you find it useful! ⭐**
