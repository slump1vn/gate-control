## ADDED Requirements

### Requirement: Access event record
The system SHALL record one access event for every decision, including denials, shadow-mode decisions, errors and manual overrides. Each event SHALL store the timestamp, gate, raw and normalised plate, confidence, frames read and frames agreed, the matched vehicle (nullable), the near-miss vehicle (nullable), the decision, the machine-readable reason, the captured frame reference (nullable), whether a command was sent and its result, the decision latency, and the operator identity for manual overrides.

#### Scenario: Every decision produces exactly one event
- **WHEN** a decision request completes for any reason, including timeout or a disabled gate
- **THEN** exactly one access event SHALL be written

#### Scenario: Captured frame linked
- **WHEN** a decision produced a usable plate read
- **THEN** the frame that produced the winning read SHALL be persisted as an `UploadedImage` with `source='gate'` and linked from the event, and the remaining burst frames, including frames that finish after the decision, SHALL be deleted with their files

#### Scenario: Gate frames are not publicly accessible
- **WHEN** an anonymous client requests the image list, image detail or download endpoints
- **THEN** gate frames SHALL NOT be listed and requests for them by id SHALL return `404`; gate frames SHALL be served only by the operator-authenticated event image endpoint

#### Scenario: Orphaned frames removed
- **WHEN** the retention job finds gate frames older than one hour that no event references
- **THEN** it SHALL delete them

#### Scenario: Reason preserved for operator diagnosis
- **WHEN** a decision is denied
- **THEN** the event reason SHALL distinguish `no_plate`, `no_consensus`, `low_confidence`, `not_registered`, `expired`, `not_yet_valid`, `inactive`, `device_disabled`, `inference_timeout` and `processing_error` (every frame failed in the pipeline)

### Requirement: Access event API
The system SHALL expose `GET /api/v1/access-events/` returning a paginated, read-only event log filterable by gate, decision, reason and time range, and searchable by plate.

#### Scenario: Events are read-only over the API
- **WHEN** a client attempts to create, modify or delete an access event through the API
- **THEN** the system SHALL respond `405`

#### Scenario: Filtering by decision
- **WHEN** a client requests events filtered by `decision=denied`
- **THEN** the response SHALL contain only denied events, newest first

### Requirement: Retention
The system SHALL delete access events and their linked images once they exceed `GATE_EVENT_RETENTION_DAYS`, via a scheduled job running alongside the existing retry scheduler.

#### Scenario: Expired events purged
- **WHEN** the retention job runs and finds events older than the retention window
- **THEN** it SHALL delete those events and the image files and `UploadedImage` rows they reference, and SHALL log the number deleted

#### Scenario: Aggregate metrics survive purging
- **WHEN** events are purged
- **THEN** the Prometheus decision counters SHALL be unaffected

### Requirement: Gate decision metrics
The system SHALL expose gate activity on the existing `/metrics/` endpoint.

#### Scenario: Metric series present
- **WHEN** Prometheus scrapes the application
- **THEN** it SHALL find `lpr_gate_decisions_total` labelled by gate, decision and reason; `lpr_gate_decision_duration_seconds`; `lpr_gate_commands_total` labelled by gate, command and result; and `lpr_gate_controller_up` labelled by gate
