## Context

The gate stack is live in shadow mode at the west gate: two cameras, `gate-agent`, decisions in `lpr-app`, the command queue, and the simulated barrier as the reference implementation of the controller contract (`2026-09-22-anpr-gate-automation`, design §6 and §13). The physical actuator has not been built. The barrier is an APS `BR6_CT VER:001` AC-motor board (design §6a). Its `COMMUNICATION MODULE` header most likely carries the 433 MHz receiver for the guards' handheld remotes (*verify*, task 0.2).

The site wants the actuator to be an ESP32-S3 with a CC1101 433 MHz transceiver that behaves like one more remote, on the internal WiFi network. This document covers what differs from the relay design. Everything not mentioned here (the decision layer, the agent's command relay, shadow mode, the closing strategy of design §7) is unchanged.

## Goals / Non-Goals

**Goals**
- Open (and, where allowed, close and stop) the barrier from the existing agent, through the existing controller contract, with no wiring into the controller's command terminals.
- Keep every fail-safe property of design §6 that still has a meaning over radio: idle is safe, the device enforces the interlock and rate limit itself, a watchdog guards against hangs, and commands cannot be replayed.
- Be honest about what the system knows: a radio command is *sent*, not *confirmed*, unless the arm's limit outputs are wired.
- Keep the guards' remotes and push-buttons working exactly as they do today.

**Non-Goals**
- Breaking or reimplementing rolling-code (KeeLoq/HCS) remotes. If the site's remotes are rolling-code, this approach stops and the relay design is built instead.
- Django contacting the device. The agent stays the only thing that sends commands (design §1).
- OTA firmware updates in the first version. Flashing is over USB. Signed OTA is a later change.
- A second RF use (reading other remotes, logging who opened the gate by remote). Capture exists only in setup mode, for the one-off cloning fallback.

## Decisions

### 1. Emulate a remote; prefer pairing a new one over cloning

Barrier receivers on 433.92 MHz almost always decode one of two families:

| Family | Typical chips | What the ESP32 can do |
|---|---|---|
| **Fixed / learning code** (OOK, ~24-bit frames) | EV1527, HS1527, PT2262, RT1527 | Transmit any code the receiver accepts. With a **learning receiver**, pair the ESP32 as a *new* remote with its own randomly generated 20-bit address. |
| **Rolling code** | HCS301 (KeeLoq), HCS200 | Nothing usable. A replayed frame is rejected, and a replay can desynchronise the guard's remote. |

Order of preference:

1. **Pair a new remote (preferred).** Setup mode generates a random EV1527 address and holds it in NVS. The installer presses the receiver's LEARN button, then presses "Pair UP / DOWN / STOP" on the device's setup page, which transmits each button frame for a few seconds. The ESP32 then has its own identity at the receiver. *Caveat:* many receivers can only forget remotes by erasing all of them, which would also erase the guards' remotes, so revoking the ESP32 alone may not be possible (task 0.3).
2. **Clone a guard's remote (fallback).** For receivers that cannot learn, or where the installer prefers it. In setup mode the CC1101 receives, the firmware decodes the frame (timing, bit count, code), and the installer stores it per button after two identical captures. The ESP32 is then indistinguishable from that remote.
3. **Rolling code → relays.** Build design §6 instead. Everything above the actuator is shared, so no other work is lost.

Each of UP, DOWN and STOP must have its **own** code. Some barrier remotes have a single button that cycles open → stop → close. If that is all this receiver understands, the device cannot know which action a press produces. It is then treated like the rolling-code case: relays (task 0.2).

### 2. Hardware

| Part | Choice | Notes |
|---|---|---|
| MCU | ESP32-S3-DevKitC-1 (N8R2 or N16R8) | 2.4 GHz WiFi, native USB for flashing and logs, hardware watchdog, flash encryption available |
| Radio | CC1101 module **labelled 433 MHz** (e.g. Ebyte E07-M1101D-SMA) | The module's matching network is tuned per band. A 315/868 MHz module transmits poorly at 433. 3.3 V only |
| Antenna | 433 MHz SMA whip, or a 17.3 cm quarter-wave wire | Outside or at the wall of any metal enclosure |
| Power | 5 V ≥ 1 A adapter on its own socket, into the DevKit's USB/5V pin | The barrier's supply is never tapped |
| Enclosure | IP65 plastic box, mounted within ~10 m and line of sight of the controller cabinet | Plastic, not metal: the radio has to get out |
| Optional | 2 wires from `UP LIMIT OUTPUT` / `DOWN LIMIT OUTPUT` to GPIOs, *only if dry contacts* (design §6a, task 0.4e of the base change) | Adds arm-position feedback; the only work inside the cabinet, low-voltage and optional |

Wiring, ESP32-S3 (FSPI default pins) ↔ CC1101:

| CC1101 | ESP32-S3 | |
|---|---|---|
| VCC | 3V3 | never 5 V |
| GND | GND | |
| SCK | GPIO12 | SPI clock |
| MOSI (SI) | GPIO11 | |
| MISO (SO) | GPIO13 | |
| CSN | GPIO10 | |
| GDO0 | GPIO4 | TX data in asynchronous (OOK) mode |
| GDO2 | GPIO5 | RX data, capture mode only |
| — | GPIO6 / GPIO7 | optional up/down limit inputs (pull-up, contact to ESP32 GND) |
| — | GPIO0 (BOOT) | held at power-on for 5 s: setup mode |

The pins are compile-time constants in one header, so a different board is a one-file change.

### 3. Radio

- CC1101 in **asynchronous serial OOK mode** at the frequency measured in task 0.1 (normally 433.92 MHz). The firmware bit-bangs the frame on GDO0 with microsecond timing from the stored profile (short/long pulse widths, sync gap, bit count). This is the same encoding RCSwitch uses for EV1527/PT2262 (its protocol 1: 350 µs base), so the encoder can be tested on the host against RCSwitch's known frames.
- A remote repeats its frame for as long as the button is held. A command therefore sends the frame **`RF_REPEATS` times (default 10, about 400-500 ms)**, the radio equivalent of the relay design's `PULSE_MS` press.
- **Transmit power ≤ +10 dBm (≈10 mW)**, the CC1101's `PATABLE` setting for 433 MHz, which is also the typical limit for short-range devices in 433.05-434.79 MHz. Task 0.5 confirms the current Vietnamese regulation. Range is raised by antenna placement, not by power.
- **Transmitter never left keyed.** The TX routine runs with a hard deadline (`RF_MAX_TX_MS`, 1500 ms). The CC1101 goes back to IDLE at the end of every frame burst, on any error, at boot and on watchdog reset.
- **Idle is silent.** Nothing is transmitted unless a validated command is being executed. Power, WiFi or agent loss produces no RF at all, and the barrier stays where it is.

### 4. Firmware

```
gate-controller/
  platformio.ini        envs: esp32s3 (device), native (host tests)
  include/pins.h        the wiring, in one place
  lib/gatecore/src/     pure C++, no hardware; built for both envs
    crypto.{h,cpp}      SHA-256, HMAC-SHA256, constant-time compare
    guard.{h,cpp}       signature, clock, ts window, nonce, interlock, rate limit
    rf_frame.{h,cpp}    EV1527/PT2262 frame encoder, capture decoder
  src/
    main.cpp            setup/loop, watchdog, mode switch, status LED, shared state
    config.{h,cpp}      NVS: WiFi, gate_id, secret, heartbeat URL, RF codes, dry run; the nonce
    radio.{h,cpp}       CC1101 async OOK; RMT-timed transmit with a hard cap; GDO2 capture
    net.{h,cpp}         WiFi recovery ladder, SNTP, signed heartbeat task
    api.{h,cpp}         /open /close /stop /status (contract v2)
    arm.{h,cpp}         optional limit inputs -> arm_state, open confirmation
    portal.{h,cpp}      access point + setup page (setup mode only)
  test/test_core/       host unit tests for lib/gatecore
```

Transmit timing uses the RMT peripheral (1 µs ticks), not bit-banging. WiFi interrupts cannot stretch a pulse, and the peripheral wait's timeout, not code that might hang, cuts the transmission.

- **Libraries:** `SmartRC-CC1101-Driver-Lib` (CC1101 registers, async mode), `ArduinoJson`, the ESP-IDF HTTP server via Arduino, `mbedtls` (bundled) for HMAC-SHA256. The encoder is our own and is tested against RCSwitch reference frames.
- **Normal mode** serves only the command API and sends heartbeats. There is no web page and no capture.
- **Setup mode** starts on BOOT held for 5 s at power-on, or on first boot with no config. The device opens an access point `gate-ctl-<mac>`, whose WPA2 password is generated on first boot and printed on the USB console for the device label. Its page sets WiFi, heartbeat URL, `gate_id` and device secret; pairs UP/DOWN/STOP; captures a remote (fallback); test-transmits each button; and toggles dry-run. Setup mode refuses all command API calls and restarts into normal mode after 10 minutes without use.
- **Dry-run** (NVS flag, on by default after provisioning) runs every check and logs "would transmit UP" without keying the radio. It is turned off at the site once the bench test has passed.
- **Secrets at rest:** device secret and WiFi password in NVS. Flash encryption (task 2.10) is a one-way eFuse operation done when flashing a production unit, not a build setting. Until it is done, a stolen device gives up its secret over USB. The token is then rotated in `/manage/gates` and the device re-provisioned.

### 5. Controller contract v2: signed commands

Over WiFi, the v1 bearer token would travel in clear in every command and every heartbeat. Anyone on that network could capture it once and then drive the barrier with fresh nonces. v2 keeps the endpoints and error codes and replaces the bearer with a signature:

```
X-Gate-Nonce: <monotonic integer>
X-Gate-Ts:    <unix seconds>
X-Gate-Sig:   hex(HMAC-SHA256(secret, METHOD \n PATH \n NONCE \n TS \n sha256(body)))
```

- The device checks the signature in constant time, then the timestamp window (±30 s), then that the nonce is greater than the last accepted one. The last nonce is written to NVS on every accepted request, before acting on it, so a reboot cannot reopen old nonces. Commands are rare (one motion command per 3 s at most), well within NVS wear limits.
- `/status` and the heartbeat are signed the same way, the heartbeat by the device towards `/api/v1/gate/heartbeat/`. Django verifies it with the gate's existing encrypted controller token, which becomes the HMAC secret.
- The agent's `ControllerClient`, the simulator (the executable spec, design §13) and the firmware move to v2 together. The simulator accepts v1 for one release so a running agent keeps working during the rollout. The firmware only ever speaks v2.
- Command responses gain `result`: `sent` (radio, unconfirmed) or `dry_run`. The agent stores it in `command_result` as today. The agent waits 3 s for a reply, and the arm takes longer than that to travel. With arm feedback wired, confirmation therefore arrives afterwards in the heartbeat's `last_command_result` (`open: ok` or `open: not_confirmed`), not in the command's reply. `ok` / `not_confirmed` stay valid results for a controller that can confirm within the reply.

### 6. Network

- The ESP32 joins the internal WiFi as a client. Recommended: a separate IoT SSID/VLAN. At minimum, a **DHCP reservation** (fixed IP) so `controller_url` in `/manage/gates` stays valid.
- Two flows only, and the firewall should allow nothing else: gate host (`gate-agent` on 192.168.2.80) → ESP32 TCP/80 for commands; ESP32 → `lpr-app` (192.168.2.80:8000) for heartbeats.
- WiFi loss: reconnect with exponential backoff (1 s → 30 s). If still down after 5 minutes, restart the WiFi stack, and after 15 minutes reboot. The radio stays silent throughout.
- RSSI goes in every heartbeat. Below −75 dBm the gate page shows a weak-signal warning, and the installer moves the device or adds an access point (task 6.2).

### 7. Time

The ±30 s timestamp window needs a real clock. The device syncs over SNTP (the configured server, then `pool.ntp.org`) and cross-checks against `server_ts` in every heartbeat reply, correcting itself if it is more than 5 s off. A device with no clock at all signs its first heartbeat with a 1970 timestamp. The service refuses it, but because the signature is genuine it answers `403 CLOCK_SKEW` with `server_ts`, and the device sets its clock from that. A forged time can only make the device refuse commands: a replay still needs an unseen nonce. Until the clock is synced, commands are refused with `503 clock_not_synced`, visible in `/status` and in the heartbeat (`clock_synced`).

### 8. Server and agent changes

- `GateDevice.CONTROLLER_TYPES` adds `('esp32_rf', 'ESP32 + 433 MHz remote')`. Behaviour is the same as `esp32` (real hardware: moves only in `live` mode). The type exists so the UI can say "radio, unconfirmed" and hide "arm position" when no limit feedback is reported.
- Heartbeat accepts and stores `transport` (`relay`|`rf433`), `wifi_rssi`, `clock_synced`, `rf_tx_count`, `dry_run`. New gauge `lpr_gate_controller_rssi_dbm{gate}`. Alerts: `dry_run` still on while `GATE_MODE=live`, and RSSI below −80 dBm for 5 minutes.
- The gate page and `/monitor` show the result `sent` as "sent (unconfirmed)". The event log shows the command result as today.
- Heartbeat verification: v2 signature (§5), v1 bearer kept for one release.
- The agent is unchanged apart from v2 signing in `ControllerClient`.

### 9. Safety, mapped from design §6

| Relay design (§6) | Radio design |
|---|---|
| Relays idle open; power loss closes nothing | Radio idle; power loss transmits nothing |
| `PULSE_MS` momentary closure | `RF_REPEATS` frame burst, `RF_MAX_TX_MS` hard cap |
| UP/DOWN interlock `INTERLOCK_MS` | Same, in `guard.cpp` |
| Motion rate limit, STOP exempt | Same |
| Watchdog → idle | Same (task watchdog on loop and HTTP task, radio forced IDLE at boot) |
| Guard buttons in parallel | Guard buttons *and* guard remotes untouched; the ESP32 is one more remote |
| STOP relay always available | STOP only if the remote has a STOP button with its own code (task 0.2); otherwise `/stop` answers `501 not_supported`, the UI says so, and emergency stop stays with the guard's button |

The closing strategy (design §7) is unchanged: no software close without a safety input. A radio close is still a close.

### 10. What radio costs in security, compared with relays

A fixed-code 433 MHz frame can be recorded by anyone within range with a €15 SDR and replayed at will. **This is already true of the guards' remotes today**, so pairing the ESP32 adds one more code that can be recorded, not a new class of weakness. It is still weaker than relays, whose command path never leaves the cabinet. Mitigations:

- a dedicated paired code (decision 1), so the ESP32's code can at least be told apart and, receiver permitting, removed;
- low transmit power and short bursts, which shrink the window and the area in which the frame can be recorded;
- the camera still logs every vehicle, and an opening with no matching grant or manual event can be spotted afterwards (a later change could alert on "arm up with no command", if limit outputs are wired);
- if the site's risk assessment rejects this, the relay design is the answer, and nothing above the actuator changes.

### 11. Regulatory

433.05-434.79 MHz is an ISM/short-range band in Vietnam. Low-power devices in it are typically exempt from individual licensing, subject to power and duty-cycle limits set by the Ministry's short-range device circular. Task 0.5 confirms the current circular and its limits before the device is deployed. The firmware already caps power (+10 dBm) and duty cycle (bursts under 1.5 s, at most one motion command every 3 s).

## Risks / Trade-offs

- **Rolling-code or single-button remotes** make RF emulation impossible → relay design. Settled in survey task 0.2, before any purchase beyond a €20 test kit.
- **No confirmation without limit wiring**: a lost frame means the barrier does not open while the system thinks it was sent. Mitigated by repeats, antenna placement and the range test (task 5.3); fully solved only by the optional limit wiring.
- **WiFi at the gate** may be weak or congested; a command arrives late or not at all. Mitigated by RSSI monitoring, a DHCP reservation, a dedicated access point if needed, and the agent's existing single retry.
- **Receiver can only erase all remotes**: revoking the ESP32 would force re-pairing the guards' remotes. Recorded at survey (task 0.3), and planned for, not discovered during an incident.
- **Collision with a guard pressing the remote at the same moment**: two transmitters on one frequency garble each other. The agent retries once; the guard's own press works as usual.

## Migration / Rollout

Survey → bench (dry-run, then a spare receiver) → site dry-run → supervised live → unsupervised, the same staging as the base change (tasks 8.x), with radio-specific gates. **Rollback**: unplug the ESP32. Nothing is wired into the controller, so the barrier, the guards' remotes and the buttons are exactly as before. In software, `GATE_MODE=shadow` or the device's dry-run flag.

## Open Questions

- Remote type, buttons and code family: task 0.2. This decides the whole path.
- Can the receiver learn more remotes, and can it forget one? Task 0.3.
- Which WiFi network and SSID the device joins, and whether a DHCP reservation and firewall rule can be made. Needs the site's network owner.
- Are the limit outputs dry contacts, and does the site accept two low-voltage wires into the cabinet for arm feedback? Task 0.4e of the base change.
