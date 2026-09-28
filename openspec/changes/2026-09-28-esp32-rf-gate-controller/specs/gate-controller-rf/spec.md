## ADDED Requirements

### Requirement: Radio actuation
The device SHALL operate the barrier only by transmitting the stored 433 MHz frame for the requested button, as a burst of `RF_REPEATS` frames, and SHALL NOT transmit at any other time.

#### Scenario: Accepted open command
- **WHEN** the device accepts `/open`
- **THEN** it SHALL transmit the UP frame `RF_REPEATS` times, return the radio to idle, and respond `200` with `result` `sent` (or `dry_run` in dry-run mode)

#### Scenario: Open confirmed afterwards
- **WHEN** arm feedback is wired and an open was transmitted
- **THEN** the next heartbeats SHALL report `last_command_result` `open: ok` once the up limit closes, or `open: not_confirmed` if it has not closed within the confirmation timeout

#### Scenario: Transmitter cap
- **WHEN** a transmission has lasted `RF_MAX_TX_MS`
- **THEN** the device SHALL stop transmitting and return the radio to idle, whatever the state of the burst

#### Scenario: Idle is silent
- **WHEN** the device is powering up, reconnecting WiFi, recovering from a watchdog reset, in setup mode, or has lost the agent
- **THEN** it SHALL NOT transmit

#### Scenario: No STOP code
- **WHEN** `/stop` is received and no STOP frame is stored
- **THEN** the device SHALL respond `501` and SHALL NOT transmit

### Requirement: Transmit power and duty cycle
The device SHALL transmit at no more than +10 dBm and SHALL keep each burst under `RF_MAX_TX_MS` (default 1500 ms).

#### Scenario: Power setting
- **WHEN** the radio is configured at boot
- **THEN** its output power SHALL be set to +10 dBm or less, and no command SHALL raise it

### Requirement: Pairing and capture only in setup mode
The device SHALL pair (transmit its own generated code for the receiver to learn) and capture (receive and store an existing remote's frames) only in setup mode, which refuses all command API calls.

#### Scenario: Setup mode refuses commands
- **WHEN** a command arrives while the device is in setup mode
- **THEN** the device SHALL respond `503` and SHALL NOT transmit

#### Scenario: Capture confirmed by repetition
- **WHEN** a button is captured in setup mode
- **THEN** the device SHALL store it only after two identical decoded frames

#### Scenario: Setup mode times out
- **WHEN** setup mode has gone 10 minutes without a request to its page
- **THEN** the device SHALL restart into normal operation

### Requirement: Dry run
The device SHALL support a dry-run mode, enabled by default after provisioning, in which every command is validated and answered with `result` `dry_run` without transmitting.

#### Scenario: Dry-run command
- **WHEN** a valid `/open` arrives in dry-run mode
- **THEN** the device SHALL respond `200` with `result` `dry_run` and SHALL NOT transmit

### Requirement: Clock before commands
The device SHALL refuse commands until its clock is synchronised.

#### Scenario: Unsynchronised clock
- **WHEN** a command arrives and neither SNTP nor a heartbeat reply has set the clock
- **THEN** the device SHALL respond `503` with `clock_not_synced` and SHALL NOT transmit

#### Scenario: Clock from a refused heartbeat
- **WHEN** a heartbeat carries a valid signature but a timestamp outside the window
- **THEN** the service SHALL refuse it with `403` `CLOCK_SKEW` and include `server_ts`, and the device SHALL set its clock from it

### Requirement: WiFi resilience
The device SHALL reconnect to WiFi with backoff, restart its WiFi stack after 5 minutes offline and reboot after 15 minutes offline, transmitting nothing while offline.

#### Scenario: Prolonged WiFi loss
- **WHEN** the device has been disconnected for 15 minutes
- **THEN** it SHALL reboot into normal mode with the radio idle
