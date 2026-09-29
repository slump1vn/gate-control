## 0. Survey (decides the whole path; about half a day on site)

- [ ] 0.1 Measure the remote's frequency and modulation. Use an RTL-SDR with `rtl_433 -A` or Universal Radio Hacker, or the CC1101 test kit in capture mode. Record the centre frequency (expected 433.92 MHz), OOK/FSK, pulse widths, bit count and repeat gap.
- [ ] 0.2 Identify the code family and the buttons. Open one guard remote and read the encoder chip: EV1527/HS1527/PT2262 = fixed/learning, HCS301/HCS200 = rolling. Capture each button twice. Two identical captures of one button = fixed code. Confirm UP, DOWN and STOP each have their own code, and that no single button cycles open → stop → close. **Decision gate:** rolling code, or a single cycling button → stop here and build the relay design (base change design §6, tasks 5.x).
- [ ] 0.3 Check the receiver on the `COMMUNICATION MODULE` header: model, whether it has a LEARN button, how many remotes it holds, and how to delete one remote versus all of them. Photograph it and write it down.
- [ ] 0.4 Walk-test radio range and placement. With the test kit transmitting UP in dry-run (receiver not listening) and an SDR at the cabinet, find a mounting spot within line of sight, and check WiFi RSSI there (target ≥ −70 dBm).
- [ ] 0.5 Confirm the current Vietnamese rules for short-range devices at 433.05-434.79 MHz (power, duty cycle) and record the limits in `gate-controller/README.md`.
- [ ] 0.6 Agree with the network owner: SSID/VLAN, a DHCP reservation for the device, a firewall rule for the gate host → device:80 and device → 192.168.2.80:8000, and an NTP source.

## 1. Hardware

- [ ] 1.1 Buy per gate: ESP32-S3-DevKitC-1; a CC1101 module labelled 433 MHz with an SMA connector; a 433 MHz antenna; a 5 V ≥ 1 A adapter; an IP65 plastic enclosure; cable glands. Buy one spare set and, for bench tests, a generic 433 MHz learning receiver relay board matching the code family from 0.2.
- [ ] 1.2 Wire per design §2 (CC1101 at 3V3 only). Label the device with its id and its setup-mode AP password.

## 2. Firmware (`gate-controller/`)

- [x] 2.1 PlatformIO project with `esp32s3` and `native` environments; pin map in one header; `config` in NVS with defaults.
- [x] 2.2 `guard`: HMAC-SHA256 verification in constant time, ±30 s window, monotonic nonce persisted to NVS on every accepted request, UP/DOWN interlock, motion rate limit with STOP exempt. Host unit tests for each rule and each rejection code (401/409/429/503).
- [x] 2.3 `rf`: CC1101 async OOK setup at the configured frequency and power (≤ +10 dBm); frame encoder from a stored profile; burst of `RF_REPEATS` with a hard `RF_MAX_TX_MS` cap; radio forced IDLE at boot, after every burst and on error. Host tests: encoded pulse trains match RCSwitch reference frames for EV1527/PT2262.
- [x] 2.4 `rf` capture (setup mode only): GDO2 edge timing → decode → accept a button after two identical captures; store the profile per button.
- [x] 2.5 `api`: `/open`, `/close`, `/stop`, `/status` per contract v2; responses carry `result` (`sent`/`ok`/`not_confirmed`/`dry_run`); `/stop` answers `501` when no STOP code is stored; everything refused with `503` while the clock is not synced or in setup mode.
- [x] 2.6 `net`: WiFi client with backoff, WiFi-stack restart after 5 min and reboot after 15 min offline; SNTP plus the heartbeat's `server_time`; signed heartbeat every 10 s carrying the fields in design §8.
- [x] 2.7 `arm` (optional build flag): limit inputs → `arm_state`; `open` confirmed by the up limit within `arm::CONFIRM_MS`, reported in the heartbeat (the agent's 3 s reply timeout is shorter than the arm's travel).
- [x] 2.8 Setup portal: AP with a per-device password, provisioning form, pair UP/DOWN/STOP (a random EV1527 address, generated once), capture fallback, test-transmit per button, dry-run toggle, 10-minute timeout.
- [x] 2.9 Watchdog on the loop and heartbeat tasks; firmware version string from the git tag or commit (CI).
- [ ] 2.10 Flash encryption on production units: a one-way eFuse step at flashing time, documented in the README once tried on a spare board.

## 3. Server and agent

- [x] 3.1 `ControllerClient` signs with contract v2; tests against a v2 fake. Keep the local rate limit.
- [x] 3.2 Simulator verifies v2 (and v1 for one release); its tests become the conformance tests the firmware's host tests mirror.
- [x] 3.3 `GateDevice`: `esp32_rf` controller type; heartbeat fields `transport`, `wifi_rssi`, `clock_synced`, `rf_tx_count`, `dry_run`; migration; signed heartbeat verification (v1 kept for one release).
- [x] 3.4 Metrics and alerts: `lpr_gate_controller_rssi_dbm`; alert "dry-run while live" and "RSSI < −80 dBm for 5 min".
- [x] 3.5 SPA: "sent (unconfirmed)" wherever a command result is shown; WiFi signal, clock and dry-run on the gate status card; `esp32_rf` in `/manage/gates` with a hint to use the device's setup mode for pairing. Stories for each state.
- [x] 3.6 Docs: AGENTS.md gotchas, `gate-controller/README.md` (bill of materials, wiring table, flashing, setup mode, pairing and capture, regulatory limits, rollback), CHANGELOG.

## 4. CI

- [x] 4.1 `.github/workflows/gate-controller-build.yml`: `pio test -e native` and `pio run -e esp32s3` on changes under `gate-controller/`; upload the firmware `.bin` as an artifact (with a timeout, like the other workflows).

## 4A. Capture from the admin UI

- [x] 4A.1 Firmware: non-blocking capture (`POST /capture`, 202) with the result in an early heartbeat; `POST /capture/save`; a transmission cancels a capture; codes reported as fingerprints only.
- [x] 4A.2 Django: `ControllerJob` (one active per gate, claim TTL, result deadline), admin endpoint, agent claim and result endpoints, heartbeat `capture` / `buttons` fields (type-checked), audit of saved codes, simulated capture.
- [x] 4A.3 Agent: poll jobs, signed `capture` / `save_code` calls, report results.
- [x] 4A.4 SPA: *Remote codes* on `/manage/gates` with a countdown while listening, Save once heard; stories.
- [ ] 4A.5 Bench: capture each button of a real remote from the admin UI, save it, and check with the spare receiver that the saved code works.

## 5. Bench

- [ ] 5.1 Dry-run: the agent in `live` mode pointed at the device on the bench. Every command path returns `dry_run`; every rejection (bad signature, old ts, replayed nonce, interlock, rate limit, clock not synced) is exercised over HTTP.
- [ ] 5.2 Spare receiver board with LEDs: pair, then `/open`, `/close`, `/stop` each light the right output. Check the burst length and power on the SDR. Power-cycle and WiFi loss mid-command never leave the radio keyed.
- [ ] 5.3 Range and reliability: 200 consecutive `/open` commands to the spare receiver from the chosen mounting distance, at most 1 miss.

## 6. Site

- [ ] 6.1 Mount the device and antenna at the spot from 0.4; set the DHCP reservation; provision in setup mode with dry-run on; set `controller_type=esp32_rf`, `controller_url` and the token in `/manage/gates`; confirm heartbeats, RSSI and clock on the gate page.
- [ ] 6.2 If RSSI < −75 dBm, fix the WiFi (move the device, add an access point) before going further.
- [ ] 6.3 Pair with the barrier's receiver (guard present), or store captured codes. Test each button from the setup page. Confirm the guards' remotes still work.
- [ ] 6.4 **Supervised live**: dry-run off, `GATE_MODE=live`, guard present with remote and buttons for a full working week. Log every missed or unexpected opening.
- [ ] 6.5 **Unsupervised**: only after a clean supervised week, and with the closing-strategy conditions of the base change (design §7, task 0.4f) met.

## 7. Verification

- [ ] 7.1 `python manage.py test`, the gate-agent tests, the SPA stories and the firmware's native tests all pass in CI.
- [ ] 7.2 Rollback drill: unplug the device during supervised week; the gate works exactly as before with remotes and buttons, and the gate page shows the controller offline within 30 s.
