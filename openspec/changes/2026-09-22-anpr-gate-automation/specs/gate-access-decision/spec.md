## ADDED Requirements

### Requirement: Gate decision endpoint
The system SHALL expose `POST /api/v1/gate/decide/` accepting a multipart request with a gate identifier and one or more image frames captured from that gate's camera. The endpoint SHALL run each frame through the existing detection/OCR pipeline, apply the consensus rules, match against the vehicle registry, persist an access event, and return a decision synchronously.

#### Scenario: Response shape
- **WHEN** the endpoint completes a decision
- **THEN** the response SHALL contain `decision` (`granted` or `denied`), `reason` (machine-readable), `plate` (normalised, or null), `confidence`, `vehicle` (id and display plate, or null), `event_id`, `mode` (`shadow` or `live`), and `decision_latency_ms`

#### Scenario: Unknown or disabled gate
- **WHEN** the request names a gate that does not exist or has `is_enabled` false
- **THEN** the endpoint SHALL respond `denied` with reason `device_disabled` and SHALL still record the event

### Requirement: Consensus read
A decision SHALL be `granted` only when at least `GATE_CONSENSUS_MIN` of the submitted frames yield the same normalised plate, the best confidence among those agreeing frames is at least `GATE_MIN_CONFIDENCE`, and that plate matches a valid registry entry exactly.

#### Scenario: Agreement reached and plate registered
- **WHEN** three frames are submitted, two yield `30A12345` with confidences 0.91 and 0.84, one yields no plate, and `30A12345` is a valid registry entry
- **THEN** the decision SHALL be `granted` with reason `whitelist_hit`

#### Scenario: Frames disagree
- **WHEN** no normalised plate is produced by at least `GATE_CONSENSUS_MIN` frames
- **THEN** the decision SHALL be `denied` with reason `no_consensus`

#### Scenario: Agreement reached but confidence below floor
- **WHEN** enough frames agree on a plate but the best confidence among them is below `GATE_MIN_CONFIDENCE`
- **THEN** the decision SHALL be `denied` with reason `low_confidence`

#### Scenario: No plate detected in any frame
- **WHEN** the pipeline returns zero plate detections for every submitted frame
- **THEN** the decision SHALL be `denied` with reason `no_plate`

#### Scenario: Plate read but not registered
- **WHEN** consensus and confidence are satisfied but the normalised plate matches no registry entry
- **THEN** the decision SHALL be `denied` with reason `not_registered`, and any near-miss entry SHALL be recorded on the event

### Requirement: Shadow mode
The system SHALL support a `shadow` mode in which decisions are computed and recorded exactly as in `live` mode, but the response SHALL instruct the agent not to actuate.

#### Scenario: Grant computed in shadow mode
- **WHEN** a decision would be `granted` and the gate is in `shadow` mode
- **THEN** the response SHALL carry `decision: granted` with `mode: shadow`, the event SHALL record `command_sent` false, and the agent SHALL NOT send any command to the controller

#### Scenario: Shadow is the default
- **WHEN** `GATE_MODE` is unset
- **THEN** the system SHALL operate in `shadow` mode

### Requirement: Bounded decision time
The decision endpoint SHALL bound its total work by `GATE_DECIDE_TIMEOUT` and SHALL fail closed.

#### Scenario: Inference backend slow or unreachable
- **WHEN** the model backend does not return within the remaining time budget and the unfinished frames could still have changed the outcome
- **THEN** the endpoint SHALL respond `denied` with reason `inference_timeout` rather than waiting indefinitely, and SHALL record the event

#### Scenario: Early decision
- **WHEN** `GATE_CONSENSUS_MIN` frames agree on a plate before the other frames finish
- **THEN** the endpoint SHALL decide without waiting for the remaining frames

#### Scenario: Nearest plate per frame
- **WHEN** a frame contains more than one plate with OCR text
- **THEN** only the plate with the largest bounding box SHALL count as that frame's read

### Requirement: Manual override
The system SHALL expose `POST /api/v1/gate/override/` allowing an authenticated operator to issue `open`, `close` or `stop` for a gate regardless of any recognition result.

#### Scenario: Guard opens for an unrecognised vehicle
- **WHEN** an authenticated operator issues an `open` override
- **THEN** the system SHALL record an access event with decision `manual`, reason `manual_override`, and the operator identity, and the agent SHALL relay the command to the controller

#### Scenario: Override dispatched once through the agent
- **WHEN** a manual command is created in live mode
- **THEN** the agent command queue SHALL return it exactly once, `stop` commands before others, and the agent's reported result SHALL be stored on the event

#### Scenario: Stale override never fires
- **WHEN** a manual command has not been claimed within `GATE_COMMAND_TTL_SECONDS`
- **THEN** it SHALL be marked `expired` and SHALL NOT be returned to the agent

#### Scenario: Override in shadow mode
- **WHEN** a manual command is created in shadow mode
- **THEN** it SHALL be recorded with result `not_sent_shadow_mode` and SHALL NOT be dispatched

#### Scenario: Override is not available to the camera agent credentials
- **WHEN** the override endpoint is called without operator authentication
- **THEN** the system SHALL respond `403`
