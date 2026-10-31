# Third-party components

Project code: GPL-3.0-or-later, Copyright 2026 Gokit5 Community contributors.
Existing third-party copyright/license headers take precedence for their files.

| Component | Pinned version | Upstream / license |
|---|---|---|
| esptool | 5.6.0 | https://github.com/espressif/esptool · GPL-2.0-or-later; Python source and hash-verified official source distribution supplied in third_party/ |
| Arduino-ESP32 | 3.3.12 | https://github.com/espressif/arduino-esp32/tree/3.3.12 · LGPL-2.1; firmware build framework, not the installer toolchain |
| Bundled BLE sources | Arduino-ESP32 3.3.12, project registration fix | firmware/libraries/BLE; per-file upstream notices retained (BLEService.cpp Apache-2.0); change described in docs/ble-patch.md |
| ESP-IDF / SDK | as pinned by Arduino-ESP32 | https://github.com/espressif/esp-idf · file-specific upstream licenses, predominantly Apache-2.0 |
| ES8311 driver | included source | Espressif Systems; Apache-2.0 headers preserved in firmware/voice_key |
| pyserial | 3.5 | https://github.com/pyserial/pyserial · BSD-3-Clause |
| sounddevice / PortAudio | 0.5.6 / wheel bundled | https://github.com/spatialaudio/python-sounddevice · MIT; PortAudio notice supplied with installed package |
| NumPy | 2.5.4 | https://github.com/numpy/numpy · BSD-3-Clause plus notices for bundled components |
| PyInstaller | 6.22.3 | https://github.com/pyinstaller/pyinstaller · GPL with bootloader exception; build tool |
| CPython / Tcl-Tk | build interpreter | https://www.python.org/ · PSF and included Tcl/Tk terms |

`licenses/dependencies.json` records the exact locally collected Python dependency versions and upstream project URLs. License and notice files are collected without changing their text. Source builds reproduce dependencies using the pinned requirement files and upstream framework tag; preserve those notices when redistributing.

## VB-CABLE is not bundled

VB-CABLE is proprietary donationware, separate from this project's license. No driver binary is bundled. When explicitly requested by the end user, the environment wizard downloads the unmodified Pack45 ZIP from https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip, verifies the pinned SHA-256 and Windows installer signature, and opens the publisher's visible installer with Windows administrator approval. No certificate is imported and no silent-install switch is assumed. The wizard identifies VB-Audio and links its donation and licensing pages before installation: https://vb-audio.com/Cable/ and https://vb-audio.com/Services/licensing.htm . The downloaded package's own readme/license remains intact. This project does not relicense the driver or grant blanket redistribution rights. Publisher terms were checked 2026-10-31; recheck them before bundling or wider distribution.

## Release review

Publish the matching source archive beside every binary release. Do not strip dependency notices. When changing dependencies or packaging/linking, recheck applicable source/relinking obligations and update the inventory; this document is an inventory, not a blanket grant for third-party components.
