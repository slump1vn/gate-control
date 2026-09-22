## ADDED Requirements

### Requirement: Camera configuration record
The system SHALL store each gate camera's connection settings as a database record managed from the admin UI, not as environment variables. The record SHALL hold name, enabled flag, host (IPv4 address or hostname), RTSP port, HTTP port, username, encrypted password, vendor (`hikvision`, `dahua` or `generic`), main stream path, sub stream path, snapshot path, a prefer-snapshot flag, a region of interest as normalised coordinates (0–1), trigger tuning (motion threshold, settle time, cooldown), a configuration version, and the user and time of the last change.

#### Scenario: Default ports
- **WHEN** an admin creates a camera without specifying ports
- **THEN** the RTSP port SHALL default to 554 and the HTTP port to 80

#### Scenario: Vendor preset fills paths
- **WHEN** an admin selects vendor `hikvision` on a camera with empty paths
- **THEN** the main, sub and snapshot paths SHALL be pre-filled with `/Streaming/Channels/101`, `/Streaming/Channels/102` and `/ISAPI/Streaming/channels/101/picture`, and SHALL remain editable

#### Scenario: Invalid host or port rejected
- **WHEN** an admin saves a camera whose host is not a valid IPv4 address or hostname, or whose port is outside 1–65535, or whose host contains a scheme or embedded credentials
- **THEN** the save SHALL be rejected with a field-level validation error

#### Scenario: Configuration version increments
- **WHEN** any field of a camera is saved with a changed value
- **THEN** its `config_version` SHALL increase by one

#### Scenario: Camera assigned to a gate
- **WHEN** an admin assigns a camera to a gate device
- **THEN** the gate SHALL use that camera's settings from the next agent config sync, without a restart

### Requirement: Camera credential protection
The camera password SHALL be encrypted at rest using `GATE_CONFIG_ENCRYPTION_KEY`, and SHALL be write-only from every human-facing interface.

#### Scenario: Password never returned to the UI
- **WHEN** any admin or operator API returns a camera
- **THEN** the response SHALL include `password_set` (boolean) and SHALL NOT include the password in any form

#### Scenario: Blank password keeps the stored one
- **WHEN** an admin saves a camera with the password field empty
- **THEN** the stored encrypted password SHALL remain unchanged

#### Scenario: Stored value is not plaintext
- **WHEN** the database row for a camera is read directly
- **THEN** the password column SHALL contain ciphertext, not the password

#### Scenario: Encryption key missing
- **WHEN** an admin attempts to save a password and `GATE_CONFIG_ENCRYPTION_KEY` is not configured
- **THEN** the save SHALL be rejected with an error stating the key is missing, and no password SHALL be stored

#### Scenario: Credentials redacted in logs
- **WHEN** Django or the agent logs any camera URL
- **THEN** the password SHALL be replaced with `***`

### Requirement: Network allowlist for camera hosts
The system SHALL only accept camera hosts whose resolved address falls within `GATE_CAMERA_ALLOWED_CIDRS` (default `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), checked when saving and when testing.

#### Scenario: Private address accepted
- **WHEN** an admin saves a camera with host `192.168.1.64`
- **THEN** the save SHALL succeed

#### Scenario: Address outside the allowlist rejected
- **WHEN** an admin saves or tests a camera whose host is, or resolves to, an address outside the allowed CIDRs — for example `172.87.80.80` under the default allowlist
- **THEN** the request SHALL be rejected with an error naming the allowlist setting, and no network connection SHALL be made

#### Scenario: Allowlist extended explicitly
- **WHEN** `GATE_CAMERA_ALLOWED_CIDRS` includes `172.87.80.0/24`
- **THEN** a camera with host `172.87.80.80` SHALL be accepted

### Requirement: Camera connection test
The system SHALL provide `POST /api/v1/gate/cameras/test/` for admins, accepting either a saved camera id or unsaved camera values, and reporting whether the camera is reachable and authenticates, with a live preview.

#### Scenario: Successful test
- **WHEN** the RTSP port accepts a TCP connection and the snapshot URL returns a JPEG after authentication
- **THEN** the response SHALL report each step as passed and SHALL include the snapshot image as a preview, and the preview SHALL NOT be stored

#### Scenario: Digest authentication
- **WHEN** the camera answers the snapshot request with a Digest authentication challenge
- **THEN** the test SHALL authenticate using Digest, and SHALL fall back to Basic only when Digest is not offered

#### Scenario: Wrong password
- **WHEN** the camera rejects the credentials
- **THEN** the response SHALL report the authentication step as failed with a message indicating wrong username or password

#### Scenario: Testing unsaved values
- **WHEN** an admin tests values that have not been saved and leaves the password blank for an existing camera
- **THEN** the test SHALL use the stored password with the unsaved values, and SHALL NOT modify the stored record

#### Scenario: Bounded outbound request
- **WHEN** the snapshot URL redirects, exceeds 5 seconds, returns more than 5 MB, or returns a non-JPEG content type
- **THEN** the test SHALL stop and report the corresponding failure without following the redirect or reading further

#### Scenario: Result recorded on the camera
- **WHEN** a test is run against a saved camera id
- **THEN** the camera's last test time, pass/fail and error message SHALL be updated

### Requirement: Region of interest selection
The admin UI SHALL let an admin set the camera's region of interest by drawing a rectangle on the test-connection preview, stored as normalised coordinates.

#### Scenario: ROI drawn on preview
- **WHEN** an admin drags a rectangle over the preview snapshot and saves
- **THEN** the camera SHALL store the rectangle as `roi_x`, `roi_y`, `roi_w`, `roi_h` in the 0–1 range relative to the image size

#### Scenario: No ROI set
- **WHEN** a camera has no ROI configured
- **THEN** the agent SHALL use the full frame

### Requirement: Agent configuration delivery
The system SHALL provide `GET /api/v1/gate/agent-config/`, authenticated only by `GATE_AGENT_TOKEN`, returning every enabled gate with its camera configuration including the decrypted password.

#### Scenario: Unchanged configuration
- **WHEN** the agent requests configuration and presents the current configuration version
- **THEN** the endpoint SHALL respond `304` without a body

#### Scenario: Wrong credential
- **WHEN** the endpoint is called with a user session or a device token instead of the agent token
- **THEN** it SHALL respond `403`

#### Scenario: Change applied without restart
- **WHEN** an admin saves a camera change
- **THEN** the agent SHALL reconnect to the camera with the new settings within 30 seconds, without a container restart

### Requirement: Configuration change audit
Every create, update or delete of a camera or gate device, through either the SPA or Django admin, SHALL record a change entry with the user, time, object, and the changed fields with old and new values.

#### Scenario: Password change audited without the value
- **WHEN** an admin changes a camera password
- **THEN** the change entry SHALL record `password` as `changed` and SHALL NOT contain the old or new password

#### Scenario: Change history visible to admins
- **WHEN** an admin opens a camera's page
- **THEN** the UI SHALL show that camera's change history, newest first
