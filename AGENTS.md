# Community release boundaries

- This directory is the public project. Never copy the parent project's backups, local-config, logs, tools, downloads, vault, factory dumps, or private device identifiers into it.
- All device data belongs under the current user's Gokit5Community data directory, separately by hardware MAC. Do not embed credentials or share a factory backup between devices.
- Firmware flashing must remain backup-first: two complete identical reads, profile confirmation, disabled security protections, identity recheck, bundle hash verification, then the sole write operation. Never add erase-all, force, eFuse writes, or protection bypasses.
- Hardware compatibility is restricted to the documented four-key profile. Do not infer GPIO compatibility from ESP32-S3 identification alone.
- This is an alpha. Distinguish unit/package tests, chip startup, Bluetooth pairing, actual voice transcription, second-device installation, recovery and long-running tests.
- Source and dependency license notices must accompany binary redistribution. Do not bundle VB-CABLE installers without a separately verified redistribution grant.
- Do not publish remotely without explicit user instruction.
