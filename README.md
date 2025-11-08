# BLE DMM -> Wi-Fi Bridge

Convert AN9002 / F-9788 style Bluetooth multimeters into Wi-Fi meters by dropping in an ESP32. This repository now contains:

1. **Production firmware** that powers the ESP32 from the meter, decodes the AN9002 payload, and serves a small web dashboard/API.
2. **Python helpers** for sniffing BLE packets, validating the XOR + bit-reversal logic, and experimenting with richer dashboards during bring-up.

Everything is structured for an open-source release--credentials removed, directories clarified, and a permissive license included.

---

## Repository Layout

| Path | Purpose |
| --- | --- |
| `firmware/wifi_multimeter/wifi_multimeter.ino` | ESP32 sketch that reads the meter's UART stream, auto-gates the data-enable pin, connects to Wi-Fi, and exposes HTML/JSON endpoints. |
| `python/ble_dmm_min.py` | Minimal bleak client for verifying connectivity and decoding logic from a desktop. |
| `python/BLE with webui.py` | Bleak + aiohttp bridge that mirrors the firmware features in Python (HTML dashboard, JSON + SSE). |
| `python/Raw BLE data.py` | Dumps raw BLE notifications alongside XOR-decoded bytes for reverse-engineering. |
| `python/requirements.txt` | Dependencies shared by the Python helpers (`bleak`, `aiohttp`). |
| `.gitignore`, `LICENSE`, `README.md` | Publishing basics: keeps the repo clean, defines licensing, and documents the project. |

---

## Hardware Conversion (High Level)

1. **Open the multimeter** - Push on the battery tray to pop the shell, remove screws, and lift the board.
2. **Remove the stock Bluetooth module (F-9788)** - Hot air or careful desoldering; clean the pads.
3. **Wire the ESP32**  
   - Meter UART TX -> ESP32 `GPIO17` (sketch `RX_PIN`).  
   - Meter "Bluetooth enable / data" pad -> ESP32 `DATA_EN_PIN` (default `GPIO6`, change if your module reserves it for flash).  
   - Shared ground between the meter and ESP32.  
4. **Power the ESP32** - Feed the board from the AA pack through a boost converter with an enable pin (e.g., TPS61023). Tie the enable pin to the same pad as `DATA_EN_PIN` so turning the meter's Bluetooth mode on/off powers the ESP32.  
5. **Flash the firmware**, enable Wi-Fi, and put the meter back together. When you toggle Bluetooth mode, the ESP32 boots, listens to the UART traffic, and serves the live reading over HTTP.

> The `DATA_EN_PIN` logic mirrors the original module: it stays asserted while bytes arrive and releases after 30 s of inactivity to save power.

---

## ESP32 Firmware Quick Start

1. **Configure Wi-Fi & pins** in `firmware/wifi_multimeter/wifi_multimeter.ino`:  
   ```cpp
   static const char* WIFI_SSID = "<YOUR_SSID>";
   static const char* WIFI_PASS = "<YOUR_PASS>";
   static const int   RX_PIN    = 17;
   const  int         DATA_EN_PIN = 6; // change if GPIO6 is unavailable
   ```
2. **Open the sketch** in Arduino IDE or PlatformIO. Make sure the sketch folder (`firmware/wifi_multimeter/`) matches the filename.
3. **Select your ESP32 board** (tested on ESP32-WROOM modules) and flash. Default serial debug baud is `115200`.
4. After boot, the module hosts:
   - `GET /` - live numeric readout + mini chart (OBS/browser friendly).
   - `GET /api/latest` - JSON payload: `{"value":"1.234","unit":"V","functions":"DC Auto"}`.
   - `GET /api/debug` - last decoded frame in hex/bits to help remap icons.
5. Optional: configure a static IP in `beginWifi()` if your router is slow to lease.

### Pin/Signal Reference

| Signal | Default ESP32 pin | Notes |
| --- | --- | --- |
| Meter TX -> ESP32 RX | `GPIO17` | Set `RX_PIN` if you relocate. Only RX is required. |
| ESP32 -> Meter enable | `GPIO6` (`DATA_EN_PIN`) | Drives the pad that originally toggled the Bluetooth module. Move to a safe GPIO (e.g., 4/5/16/23) on boards where 6-11 are flash pins. |
| Ground | any GND pin | Must be shared for UART and enable control to work. |

---

## Python Debug Tools

The Python scripts live in `python/` and share the same decode logic as the firmware. They're great for lab debugging or confirming a new meter revision before soldering.

### Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r python/requirements.txt
```

### Usage

| Script | Command | What you get |
| --- | --- | --- |
| `python/ble_dmm_min.py` | `python python/ble_dmm_min.py` | Connects to one BLE meter with bleak and prints decoded values with timestamps. |
| `python/BLE with webui.py` | `python "python/BLE with webui.py"` | BLE client + aiohttp server exposing `/`, `/api/latest`, and `/stream` (SSE) for rapid prototyping. |
| `python/Raw BLE data.py` | `python "python/Raw BLE data.py"` | Hexdumps raw notifications, XOR-decoded payloads, and optional bit-reversed bytes to help map the protocol. |

> Update `TARGET_NAME`/`TARGET_ADDR_STR` (or `ADDRESS`/`CHAR`) in each script before running.

---

## Release / Publishing Checklist

- [x] **Secrets scrubbed** - `wifi_multimeter.ino` ships with placeholder SSID/passwords; never commit real credentials.
- [x] **Clear directory structure** - firmware and helper scripts live in separate folders with descriptive names.
- [x] **Reproducible setup** - `python/requirements.txt` documents Python deps; Arduino sketch is self-contained.
- [x] **Licensing & ignore rules** - MIT license and a repo-friendly `.gitignore` are included.
- [ ] **(Optional)** Add photos/wiring diagrams or a demo GIF before tagging a release.

---

## References & Further Reading

- Bluetooth-DMM Wiki - https://github.com/ludwich66/Bluetooth-DMM/wiki  
- AN9002 protocol deep-dive - https://justanotherelectronicsblog.com/?p=930  
- F-9788 module manual - https://fccid.io/2AR7VF-9788/User-Manual/15-F-9788-UserMan-r1-4697443.pdf

Happy measuring!
