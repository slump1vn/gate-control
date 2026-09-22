## ADDED Requirements

### Requirement: Controller command protocol
The barrier controller device SHALL expose `POST /open`, `POST /close`, `POST /stop` and `GET /status` over HTTP on the gate LAN. Every command request SHALL carry a bearer token, a monotonic `nonce` and a timestamp `ts`.

#### Scenario: Valid open command
- **WHEN** the device receives `/open` with a valid token, an unseen nonce and a timestamp within ±30 seconds of device time
- **THEN** the device SHALL pulse the UP relay for `PULSE_MS` and respond `200` with the resulting state

#### Scenario: Replayed command rejected
- **WHEN** the device receives a command carrying a nonce it has already accepted
- **THEN** the device SHALL reject it with `409` and SHALL NOT actuate any relay

#### Scenario: Stale timestamp rejected
- **WHEN** a command's `ts` is outside the ±30 second window
- **THEN** the device SHALL reject it with `401` and SHALL NOT actuate any relay

#### Scenario: Missing or wrong token
- **WHEN** a command arrives without a valid bearer token
- **THEN** the device SHALL respond `401` and SHALL NOT actuate any relay

### Requirement: Momentary actuation
The device SHALL drive the barrier only through momentary closures between `COM` and the corresponding `UP`, `DOWN` or `STOP` terminal, of duration `PULSE_MS`. The device SHALL NOT hold any contact closed beyond a single pulse.

#### Scenario: Pulse duration
- **WHEN** any motion command is accepted
- **THEN** the corresponding relay SHALL close for `PULSE_MS` (default 400 ms) and then open

### Requirement: Fail-safe idle state
All relays SHALL be open whenever the device is not executing a pulse, including at power-on, after a watchdog reset, and while WiFi or the agent is unreachable.

#### Scenario: Power loss
- **WHEN** the device loses power
- **THEN** no contact SHALL be closed, the barrier SHALL hold its current position, and the manual push-buttons SHALL continue to operate the barrier

#### Scenario: Network loss
- **WHEN** the device loses its WiFi association
- **THEN** it SHALL return to the idle state, SHALL NOT actuate any relay, and SHALL attempt reconnection with backoff

#### Scenario: Firmware hang
- **WHEN** the main loop stops servicing the hardware watchdog
- **THEN** the device SHALL reset and boot into the idle state

### Requirement: Safety interlocks
The firmware SHALL enforce safety constraints locally, independent of any instruction it receives.

#### Scenario: UP/DOWN interlock
- **WHEN** a `close` command arrives within `INTERLOCK_MS` of an accepted `open` command (or vice versa)
- **THEN** the device SHALL reject it with `409` and SHALL NOT energise the opposing relay

#### Scenario: Command rate limit
- **WHEN** a motion command arrives within `MIN_COMMAND_INTERVAL_MS` of the previous accepted motion command
- **THEN** the device SHALL reject it with `429`

#### Scenario: Stop is never rate limited
- **WHEN** a `/stop` command arrives at any time with valid authentication
- **THEN** the device SHALL pulse the STOP relay regardless of the motion rate limit

#### Scenario: Manual controls unaffected
- **WHEN** the system is in any state, including a rejected or failed command
- **THEN** the guard's existing push-buttons SHALL remain electrically able to operate the barrier

### Requirement: Wiring matches the controller's input polarity
Each relay SHALL be wired so that a firmware pulse produces the controller's "active" condition for that input, as established by measurement at the site.

#### Scenario: Normally-open command input
- **WHEN** a controller input is normally-open to `COM` (idle voltage present)
- **THEN** the relay's NO contact SHALL be wired in parallel with the existing button across that input and `COM`

#### Scenario: Normally-closed STOP input
- **WHEN** the controller's `STOP` input is normally-closed to `COM` (held closed at idle by the existing wiring)
- **THEN** the STOP relay's NC contact SHALL be wired in series in the STOP line, so that a pulse opens the loop, and a powered-down relay board SHALL leave the loop closed

#### Scenario: Existing controls preserved
- **WHEN** the relays are installed
- **THEN** the existing cable to the guard's controls SHALL remain connected and functional

### Requirement: Arm position feedback
Where the controller's up/down limit outputs are wired to the device, the device SHALL report the arm position, and an `open` SHALL be confirmed by the arm reaching the up limit.

#### Scenario: Open confirmed by limit switch
- **WHEN** the device pulses UP and the up-limit contact closes within the confirmation timeout
- **THEN** the command result SHALL be `ok` and the reported `arm_state` SHALL be `up`

#### Scenario: Open not confirmed
- **WHEN** the device pulses UP and the up-limit contact does not close within the confirmation timeout
- **THEN** the command result SHALL be `not_confirmed`, and the operator UI SHALL show it

#### Scenario: Arm state in heartbeat
- **WHEN** the limit outputs are wired
- **THEN** every heartbeat SHALL carry `arm_state` (`up`, `down`, `moving` or `unknown`)

### Requirement: Controller heartbeat
The device SHALL POST a heartbeat to `/api/v1/gate/heartbeat/` every 10 seconds carrying its gate identifier, firmware version, uptime and last command result.

#### Scenario: Heartbeat updates device state
- **WHEN** a valid heartbeat is received
- **THEN** the system SHALL update the gate's `last_seen` and `firmware_version` and set the `lpr_gate_controller_up` gauge to 1

#### Scenario: Missed heartbeats
- **WHEN** no heartbeat has been received from a gate for more than 30 seconds
- **THEN** the system SHALL set `lpr_gate_controller_up` to 0 for that gate and surface the gate as offline in the operator UI

### Requirement: Simulated controller
The system SHALL provide a simulated controller for gates whose `controller_type` is `simulator`, served at `/api/v1/gate/sim/<gate_id>/<command>`, that enforces the same contract as the device: bearer token, nonce, timestamp window, UP/DOWN interlock, motion rate limit and unrestricted stop.

#### Scenario: Agent drives the simulator like the device
- **WHEN** the agent config lists a simulated gate
- **THEN** its `controller_url` SHALL point at the simulator, and the agent SHALL send commands to it exactly as to the device

#### Scenario: Simulated arm motion
- **WHEN** the simulator accepts `open`
- **THEN** the arm SHALL report `moving`, then `up` after the configured travel time, then close automatically after the configured auto-close delay if one is set

#### Scenario: Shadow mode does not block the simulator
- **WHEN** `GATE_MODE` is `shadow` and a decision for a simulated gate is granted
- **THEN** the decision SHALL be actuated on the simulator, while a real controller in shadow mode SHALL NOT be

#### Scenario: Admin test runs
- **WHEN** an admin runs photos through the gate test page
- **THEN** the resulting event SHALL be marked `is_test`, SHALL be excluded from gate metrics, and SHALL only ever actuate a simulated barrier

### Requirement: Barrier closing strategy
The system SHALL NOT issue software-driven `close` commands unless the site's barrier controller has a safety input (loop detector or IR beam) wired and active.

#### Scenario: Controller has its own auto-close
- **WHEN** `GATE_AUTO_CLOSE` is `controller`
- **THEN** the agent SHALL never issue a `close` command and the barrier SHALL close on the controller's own timer

#### Scenario: Software auto-close without a safety input
- **WHEN** `GATE_AUTO_CLOSE` is `software` but the gate is not configured as having a safety input
- **THEN** the system SHALL refuse to start in that configuration and SHALL log a configuration error
