## MODIFIED Requirements

### Requirement: Rate limited paths
The per-IP throttle SHALL apply only to the paths listed in `RATE_LIMIT_INCLUDE_PATHS`. Gate endpoints under `/api/v1/gate/` SHALL NOT be throttled, because a throttled gate endpoint is indistinguishable from a broken barrier.

#### Scenario: Gate decision endpoint is not throttled
- **WHEN** the gate agent posts repeated decision requests to `/api/v1/gate/decide/` from the same IP, faster than `RATE_LIMIT_RATE`
- **THEN** every request SHALL be processed and none SHALL receive a `429`

#### Scenario: Gate heartbeat is not throttled
- **WHEN** the controller posts heartbeats to `/api/v1/gate/heartbeat/` every 10 seconds
- **THEN** no heartbeat SHALL receive a `429`

#### Scenario: OCR endpoint remains throttled
- **WHEN** a client posts to `/api/v1/ocr/` faster than `RATE_LIMIT_RATE`
- **THEN** the excess requests SHALL receive a `429`, unchanged from current behaviour

#### Scenario: Adding gate paths to the include list is a misconfiguration
- **WHEN** `RATE_LIMIT_INCLUDE_PATHS` is configured to include a `/api/v1/gate/` path
- **THEN** the system SHALL log a configuration warning at startup
