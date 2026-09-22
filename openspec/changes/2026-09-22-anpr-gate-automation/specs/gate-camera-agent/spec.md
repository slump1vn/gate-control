## ADDED Requirements

### Requirement: Camera ingest
The gate agent SHALL acquire frames from the gate's IP camera, preferring a still-snapshot HTTP endpoint when the camera is configured for it and answers, and falling back to the RTSP stream otherwise. The camera's address, credentials, paths and ROI SHALL come from the server's agent configuration endpoint, not from agent environment variables, and SHALL NOT appear in logs, metrics labels, or committed files.

#### Scenario: Snapshot endpoint available
- **WHEN** the agent starts and the camera answers its snapshot endpoint
- **THEN** the agent SHALL use snapshot capture for burst frames, authenticating with Digest and falling back to Basic

#### Scenario: Configuration changed in the admin UI
- **WHEN** the agent's periodic configuration sync returns a newer `config_version`
- **THEN** the agent SHALL close its camera connection and reopen it with the new settings, without restarting

#### Scenario: Configuration endpoint unreachable
- **WHEN** the configuration sync fails
- **THEN** the agent SHALL keep running with its last known configuration and retry on the next interval

#### Scenario: Camera state reported
- **WHEN** the agent's camera connection changes state
- **THEN** the agent SHALL report `streaming`, `reconnecting`, `auth_failed` or `unreachable` to the server so the admin camera page can display it

#### Scenario: Stream interruption
- **WHEN** the camera becomes unreachable or the stream drops
- **THEN** the agent SHALL retry with exponential backoff, SHALL increment `lpr_gate_camera_frames_total{result="error"}`, and SHALL NOT send any command to the controller while blind

#### Scenario: Credentials never logged
- **WHEN** the agent logs a camera URL for any reason
- **THEN** the username and password portions SHALL be redacted

### Requirement: Presence trigger
The agent SHALL NOT submit frames continuously. It SHALL detect vehicle presence by frame differencing over a configured region of interest on a low-resolution substream, and SHALL trigger a read only when motion rises and then settles.

#### Scenario: Vehicle arrives and stops
- **WHEN** the ROI difference score rises above the trigger threshold and then remains below the settle threshold for the configured settle window
- **THEN** the agent SHALL capture a burst and submit a decision request

#### Scenario: Cooldown after a decision
- **WHEN** a decision has just been returned for a gate
- **THEN** the agent SHALL NOT trigger another read for that gate until the cooldown period elapses

#### Scenario: Continuous motion without settling
- **WHEN** the ROI shows sustained motion that never settles (for example pedestrians crossing)
- **THEN** the agent SHALL NOT trigger a read

### Requirement: Burst capture and frame preparation
The agent SHALL capture `GATE_BURST_FRAMES` frames spaced by the configured interval, crop each to the camera's normalised region of interest, and encode to JPEG at normal quality within the upload limit reported by `/api/v1/config/` (2 MB by default).

#### Scenario: Frame within the limit
- **WHEN** a cropped frame encoded at normal quality is within the upload limit
- **THEN** the agent SHALL send it without further re-compression

#### Scenario: Frame exceeds upload limit
- **WHEN** an encoded frame would exceed the upload limit
- **THEN** the agent SHALL reduce JPEG quality stepwise until it fits, and SHALL log a warning if it cannot fit without dropping below the minimum quality

#### Scenario: Burst submitted as one request
- **WHEN** a burst has been captured
- **THEN** all frames SHALL be submitted in a single decision request so the server can apply consensus across them

### Requirement: Actuation dispatch
The agent SHALL send an `open` command to the controller only when the decision response is `granted` and the response mode is `live`.

#### Scenario: Granted in live mode
- **WHEN** the decision response is `granted` with mode `live`
- **THEN** the agent SHALL send one `open` command with a fresh nonce and SHALL report the command result back for the access event

#### Scenario: Granted in shadow mode
- **WHEN** the decision response is `granted` with mode `shadow`
- **THEN** the agent SHALL NOT contact the controller

#### Scenario: Decision service unreachable
- **WHEN** the decision request fails or times out
- **THEN** the agent SHALL treat it as a denial, SHALL NOT contact the controller, and SHALL increment the corresponding error metric

#### Scenario: Controller rejects or fails
- **WHEN** the controller returns a non-success response or is unreachable
- **THEN** the agent SHALL retry at most once and SHALL record the failure in the access event so the operator sees why the barrier did not move

### Requirement: Offline replay mode
The agent SHALL support a replay mode that reads frames from a directory instead of a camera, so the decision path can be exercised without site access.

#### Scenario: Replay run
- **WHEN** the agent is started in replay mode against a directory of recorded frames
- **THEN** it SHALL run the full trigger, burst, decide and dispatch logic, and SHALL never contact a controller

### Requirement: Agent observability
The agent SHALL expose Prometheus metrics on its own port, covering frames captured, triggers fired, decisions by outcome, decision latency, and controller command results.

#### Scenario: Metrics endpoint
- **WHEN** Prometheus scrapes the agent
- **THEN** the agent SHALL report `lpr_gate_camera_frames_total`, `lpr_gate_decisions_total`, `lpr_gate_decision_duration_seconds` and `lpr_gate_commands_total`
