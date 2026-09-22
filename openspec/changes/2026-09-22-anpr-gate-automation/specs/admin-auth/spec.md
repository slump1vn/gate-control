## ADDED Requirements

### Requirement: Session login for the SPA
The system SHALL provide session-based login for the SPA using Django's user model, via `POST /api/v1/auth/login/`, `POST /api/v1/auth/logout/` and `GET /api/v1/auth/me/`.

#### Scenario: Successful login
- **WHEN** a user submits valid credentials to the login endpoint
- **THEN** the system SHALL establish a session with an `HttpOnly`, `SameSite=Lax` cookie and return the user's name and roles

#### Scenario: Failed login
- **WHEN** a user submits invalid credentials
- **THEN** the system SHALL respond `401` without indicating whether the username exists

#### Scenario: Unsafe request without CSRF token
- **WHEN** an authenticated session sends a POST, PUT, PATCH or DELETE without a valid CSRF token
- **THEN** the system SHALL reject it with `403`

#### Scenario: Unauthenticated user opens an admin page
- **WHEN** a user without a session navigates to any gate management page in the SPA
- **THEN** the SPA SHALL redirect to `/login` and return to the requested page after login

### Requirement: Gate roles
The system SHALL define two roles as Django groups: `gate_admin` and `gate_operator`. Superusers SHALL be treated as holding both.

#### Scenario: Admin scope
- **WHEN** a `gate_admin` user calls camera, gate device, configuration audit, vehicle, event or override endpoints
- **THEN** the system SHALL allow the request

#### Scenario: Operator scope
- **WHEN** a `gate_operator` user calls vehicle registry, event log, manual override or emergency stop endpoints
- **THEN** the system SHALL allow the request

#### Scenario: Operator denied camera configuration
- **WHEN** a `gate_operator` user calls any camera, gate device or configuration audit endpoint
- **THEN** the system SHALL respond `403`, and the SPA SHALL NOT show those pages in navigation

#### Scenario: Anonymous denied
- **WHEN** a request without a session calls any gate management endpoint
- **THEN** the system SHALL respond `403`

### Requirement: Machine credentials are separate from users
The gate agent and gate controllers SHALL authenticate with tokens that grant access only to their own endpoints.

#### Scenario: Agent token scope
- **WHEN** a request presents `GATE_AGENT_TOKEN`
- **THEN** it SHALL be accepted on the agent configuration, decision and agent status endpoints only, and rejected with `403` everywhere else

#### Scenario: Device token scope
- **WHEN** a request presents a gate device's token
- **THEN** it SHALL be accepted on the heartbeat endpoint for that device only

### Requirement: Existing public endpoints unchanged
This change SHALL NOT add authentication to endpoints that are public today.

#### Scenario: OCR endpoint remains public
- **WHEN** an anonymous client calls `/api/v1/ocr/`, the image list or detail endpoints, health, config or metrics
- **THEN** the behaviour SHALL be unchanged from before this change
