# Gokit5 BLE patch

2026-10-30. Upstream: Espressif arduino-esp32 3.3.12, libraries/BLE.
Copyright and Apache-2.0 notices are retained in the individual source files.

`BLEService::addCharacteristic` on NimBLE previously treated any existing UUID
as the same object. HID requires multiple Report characteristics (0x2A4D) with
different Report Reference descriptors. New objects were not registered, leaving
their service pointer uninitialized; notifying the consumer report caused a
LoadProhibited panic immediately after successful authentication.

The existing-object branch now matches pointer identity. Distinct objects with
the same UUID are registered normally. No installed Arduino core files changed.

Build with `--library G:/firmware/libraries/BLE` (G: maps to this Gokit5 folder).
The firmware checks both input reports have valid distinct handles before starting
the keyboard task. The unpatched build is not a release candidate.
