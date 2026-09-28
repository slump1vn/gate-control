## MODIFIED Requirements

### Requirement: Controller command protocol
The barrier controller device SHALL expose `POST /open`, `POST /close`, `POST /stop` and `GET /status` over HTTP on the gate LAN. Every request SHALL carry `X-Gate-Nonce`, `X-Gate-Ts` and `X-Gate-Sig`, where the signature is HMAC-SHA256 with the device secret over the method, path, nonce, timestamp and the SHA-256 of the body. The secret SHALL NOT be sent over the network.

#### Scenario: Valid signed command
- **WHEN** the device receives a command with a valid signature, a nonce greater than any it has accepted and a timestamp within ±30 seconds of device time
- **THEN** the device SHALL actuate and respond `200` with the resulting state and `result`

#### Scenario: Bad signature
- **WHEN** the signature does not verify
- **THEN** the device SHALL respond `401` and SHALL NOT actuate

#### Scenario: Replayed or old nonce
- **WHEN** the nonce is not greater than the last accepted nonce, including after a reboot
- **THEN** the device SHALL respond `409` and SHALL NOT actuate

#### Scenario: Stale timestamp
- **WHEN** `X-Gate-Ts` is outside ±30 seconds
- **THEN** the device SHALL respond `401` and SHALL NOT actuate

### Requirement: Controller heartbeat
The device SHALL POST a signed heartbeat to `/api/v1/gate/heartbeat/` every 10 seconds carrying its gate identifier, firmware version, uptime, last command result, `transport`, `wifi_rssi`, `clock_synced`, `rf_tx_count` and `dry_run`.

#### Scenario: Heartbeat updates device state
- **WHEN** a heartbeat with a valid signature is received
- **THEN** the system SHALL update the gate's `last_seen`, `firmware_version` and the reported health fields, set `lpr_gate_controller_up` to 1 and `lpr_gate_controller_rssi_dbm` to the reported RSSI

#### Scenario: Dry run while live
- **WHEN** a gate's heartbeat reports `dry_run` while `GATE_MODE` is `live`
- **THEN** the gate status SHALL show it and an alert SHALL fire

## ADDED Requirements

### Requirement: Unconfirmed command results
A command result SHALL say whether the arm's movement was confirmed.

#### Scenario: Radio without arm feedback
- **WHEN** a radio controller without wired limit outputs accepts `/open`
- **THEN** the result SHALL be `sent`, and the operator UI and event log SHALL show it as sent but unconfirmed
