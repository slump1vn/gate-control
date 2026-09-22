## ADDED Requirements

### Requirement: Upload size limit
The system SHALL reject any uploaded image larger than `UPLOAD_FILE_MAX_SIZE` bytes. The default SHALL be 2 MB (2097152 bytes), and SHALL be the same in application settings, both environment example files, and every Docker Compose file.

#### Scenario: File under the limit accepted
- **WHEN** a client uploads a 1.9 MB JPEG to `/api/v1/ocr/` with default settings
- **THEN** the upload SHALL be accepted for processing

#### Scenario: File over the limit rejected
- **WHEN** a client uploads a 2.1 MB JPEG with default settings
- **THEN** the system SHALL respond `400` with error code `FILE_TOO_LARGE` and a message stating the 2.0 MB limit

#### Scenario: Limit is overridable
- **WHEN** `UPLOAD_FILE_MAX_SIZE` is set in the environment
- **THEN** the configured value SHALL be enforced instead of the default

#### Scenario: Limit exposed to the SPA
- **WHEN** a client requests `/api/v1/config/`
- **THEN** `max_upload_bytes` SHALL equal the enforced limit, so the SPA validates against the same value

#### Scenario: Uploads under the limit held in memory
- **WHEN** a file no larger than `UPLOAD_FILE_MAX_SIZE` is uploaded
- **THEN** Django SHALL keep it in memory rather than spooling it to a temporary file, because `FILE_UPLOAD_MAX_MEMORY_SIZE` equals `UPLOAD_FILE_MAX_SIZE`

### Requirement: Multi-frame requests
The limit SHALL apply to each file individually. Endpoints that accept several frames SHALL bound the number of files.

#### Scenario: Burst within limits
- **WHEN** the gate agent posts 3 frames of 1.5 MB each to `/api/v1/gate/decide/` with `GATE_BURST_FRAMES=3`
- **THEN** all frames SHALL be accepted

#### Scenario: Too many frames
- **WHEN** a request to `/api/v1/gate/decide/` contains more files than `GATE_BURST_FRAMES`
- **THEN** the system SHALL respond `400` before processing any frame

#### Scenario: One oversized frame in a burst
- **WHEN** any single frame in a decide request exceeds `UPLOAD_FILE_MAX_SIZE`
- **THEN** the system SHALL reject the request with `FILE_TOO_LARGE`, and the agent SHALL treat it as a denial
