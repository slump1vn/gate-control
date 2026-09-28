## Why

Everything upstream of the barrier is running: cameras, the presence trigger, recognition, decisions, the command queue, the simulated barrier and the monitor. What is still missing is the device that physically moves the arm. The original plan (`2026-09-22-anpr-gate-automation`, design §6, tasks 5.x) is an ESP32 with a relay board wired in parallel with the guard's buttons on the `BR6_CT` controller's `UP`/`DOWN`/`STOP` terminals. None of it has been built yet.

The site now wants a different actuator: an **ESP32-S3 with a CC1101 433 MHz transceiver** that operates the barrier the way the guard's handheld remote does, over the air to the receiver on the controller's `COMMUNICATION MODULE` header, and that talks to the system over the internal WiFi network. It needs no work inside the controller cabinet, no electrical sign-off on the command terminals, and it can be removed by unplugging it.

## What Changes

- **New firmware project `gate-controller/`** (PlatformIO, ESP32-S3, Arduino framework): WiFi client on the internal network, the existing controller HTTP contract (`/open`, `/close`, `/stop`, `/status`), the existing heartbeat, and a CC1101 driver that transmits the barrier remote's button frames (433.92 MHz OOK) instead of pulsing relays. It has a setup mode (local access point) for WiFi and token provisioning, receiver pairing and remote capture, and a dry-run mode that does everything but key the transmitter.
- **Radio strategy.** By preference, the ESP32 is **paired to the receiver as a new remote** with its own code, so it can be revoked without touching the guards' remotes. Cloning a guard's remote is a fallback. If the remotes turn out to be rolling-code (KeeLoq/HCS), RF emulation is not feasible, and the site falls back to the relay design already specified.
- **Controller contract v2: signed commands.** Over WiFi the bearer token would travel in every request. Commands are instead signed with HMAC-SHA256 over the method, path, nonce, timestamp and body, and the token itself never leaves the agent or the device. The agent's `ControllerClient` and the simulator move to v2 together with the firmware.
- **One-way actuation is reported honestly.** RF has no acknowledgement, so a command's result is `sent`, not `ok`, unless the controller's `UP LIMIT`/`DOWN LIMIT` outputs are wired to the ESP32 (optional; the existing arm-position requirement then applies unchanged). The operator UI and the event log say "sent (unconfirmed)".
- **`GateDevice.controller_type` gains `esp32_rf`** ("ESP32 + 433 MHz remote"). The heartbeat gains `transport`, `wifi_rssi`, `clock_synced` and `rf_tx_count`, shown on the gate page and exported as `lpr_gate_controller_rssi_dbm`.
- **Firmware CI**: a GitHub Actions workflow builds the firmware and runs its host-side unit tests (protocol, signature, nonce, interlock, rate limit, frame encoder), and publishes the `.bin` as a build artifact.

## Capabilities

### New Capabilities
- `gate-controller-rf`: the ESP32-S3 + CC1101 controller: radio actuation, pairing and capture, provisioning, WiFi and time, fail-safe behaviour.

### Modified Capabilities
- `gate-controller-integration`: commands are signed (contract v2); a command's result can be `sent` when the transport cannot confirm it; the heartbeat carries transport, WiFi and clock health.

## Impact

- New `gate-controller/` directory and `.github/workflows/gate-controller-build.yml`.
- `gate-agent/gate_agent/controller.py` (v2 signing), `lpr_app/services/barrier_simulator.py` and its view (v2 verification), `GateDevice` (`esp32_rf`, heartbeat fields, one migration), the heartbeat view, gate serializers, the SPA gate page and `/manage/gates` form, metrics, docs.
- Hardware per gate, about 350-600k VND: ESP32-S3 DevKit, CC1101 433 MHz module with an SMA antenna, a 5 V supply, an enclosure. Optionally two wires to the controller's limit outputs.
- No change to recognition, decisions, the command queue or shadow mode. `GATE_MODE=shadow` still never moves real hardware.
