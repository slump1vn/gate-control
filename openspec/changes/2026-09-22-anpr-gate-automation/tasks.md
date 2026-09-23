## 0. Site survey and prerequisites (blocks everything else)

- [x] 0.1 Confirm the camera addressing. **Confirmed internal** by the site. Add `172.87.80.0/24` (or `172.87.80.80/32`) to `GATE_CAMERA_ALLOWED_CIDRS` in the gate host `.env` (see 8.0). Keep the camera on an isolated VLAN with no inbound path.
- [ ] 0.2 Rotate the camera password off the guessable one supplied by the site. The new credentials are entered later through the admin camera page (section 2A) — never in the repo, a fixture, `.env`, or a compose file.
- [ ] 0.3 Identify camera make/model; confirm RTSP main/sub stream URLs and whether a still-snapshot HTTP endpoint exists. Record both in the survey notes.
- [x] 0.4 Identify the barrier controller make/model. **Identified from photos:** `BR6_CT VER:001`, APS-labelled AC-motor board. Terminal map in design.md §6a. Remaining measurements below; get the manual from APS if possible.
- [ ] 0.4a With the board powered and the arm idle, measure DC volts from `UP`, `DOWN`, `STOP` to `COM`. A voltage (typically 5–12 V) means normally-open: wire the relay NO contact across the terminal and `COM`. Record the values.
- [ ] 0.4b Determine whether `STOP` is normally-closed: if `STOP`–`COM` reads ~0 V at idle while the arm works, the button box holds it closed. The ESP32 STOP relay must then go **in series** via its **NC** contact. Confirm by briefly opening the STOP pair with the arm idle; if the controller refuses to move until it is reconnected, it is NC.
- [ ] 0.4c Establish whether the controller auto-closes today, and what each `TIMING` DIP position does. Watch the arm after a manual UP; ask the guard; check the manual.
- [ ] 0.4d Trace the 4-pair UTP cable on `COM`/`UP`/`DOWN`/`STOP` to its other end (guard button box, or another access system) and record it. It must stay connected.
- [ ] 0.4e Check the contact type of `UP LIMIT OUTPUT` / `DOWN LIMIT OUTPUT` (dry changeover C/NO/NC, or powered). Only dry contacts may go straight to ESP32 GPIOs; powered outputs need an optocoupler.
- [ ] 0.4f Decide on a safety sensor (photocell recommended, loop coil optional) for the `PHOTO` / `LOOP` inputs, which are currently empty. This is required before unsupervised operation (8.5).
- [ ] 0.5 Measure the lane: distance from the read point to the arm, and approach speed. Confirm ~3 s of recognition fits before the driver expects movement.
- [ ] 0.6 Capture a reference image set from the installed camera at the read point — day, dusk, night, rain, headlights on — and verify plates are ≥ 30 px tall (`MIN_PLATE_HEIGHT`) in the ROI crop. Re-aim the camera before writing any code if they are not.
- [ ] 0.7 Run the reference set through the existing `/api/v1/ocr/` endpoint (`python test_api.py <image>`) and record per-image read accuracy and latency. This is the baseline the whole project is judged against.
- [ ] 0.8 Confirm the on-premise host spec and which inference profile it will run (`cpu`, `nvidia-cuda`, `amd-vulkan`). Gate latency is dominated by this choice.
- [ ] 0.9 Obtain site electrical sign-off for low-voltage work on the barrier controller terminals.

## 1. Data model and registry

- [x] 1.1 Add `Vehicle` model to `lpr_app/models.py`: `plate_normalized` (unique, indexed), `plate_display`, `owner_name`, `owner_phone`, `department`, `vehicle_type` (car/motorbike/other), `valid_from`, `valid_until` (nullable), `is_active`, `notes`, `created_at`, `updated_at`.
- [x] 1.2 Add `GateDevice` model: `name`, `location`, `direction` (in/out), `camera` (FK to `Camera`), `controller_url`, `auth_token`, `has_safety_input`, `is_enabled`, `last_seen`, `firmware_version`.
- [x] 1.2a Add `Camera` model with the fields listed in design.md §10 (host, ports, username, `password_encrypted`, vendor, stream/snapshot paths, `prefer_snapshot`, normalised ROI, trigger tuning, `config_version`, `updated_by`, last test result).
- [x] 1.2b Add `GateConfigChange` model: `timestamp`, `user`, `object_type`, `object_id`, `changes` (JSON of field → old/new, password recorded only as `changed`).
- [x] 1.3 Add `AccessEvent` model: `timestamp`, `gate` FK, `plate_raw`, `plate_normalized`, `confidence`, `frames_read`, `frames_agreed`, `vehicle` FK (nullable), `near_miss_vehicle` FK (nullable), `decision`, `reason`, `uploaded_image` FK (nullable), `command_sent`, `command_result`, `decision_latency_ms`, `operator` (nullable, for manual overrides).
- [x] 1.4 Generate and apply the migration (`makemigrations lpr_app`, `migrate`); confirm it is additive and reversible.
- [x] 1.5 Register all models in `lpr_app/admin.py` with sensible list displays, search on `plate_normalized`, and filters on `is_active` / `decision`. `Camera` uses a write-only password form field and writes a `GateConfigChange` on save; `GateConfigChange` and `AccessEvent` are read-only in admin.
- [x] 1.6 Create `lpr_app/services/plate_matcher.py`: `normalize_plate(raw)` implementing positional coercion for Vietnamese plate shapes, `match(normalized)` returning an active `Vehicle` or `None`, and `find_near_miss(normalized)` returning a single edit-distance-1 candidate or `None`.
- [x] 1.7 Unit-test `plate_matcher` hard: separator variants, lowercase, confusable characters at digit and series positions, motorbike 4-character series, expired and inactive entries, and the case where two registry entries would collide after normalisation.

## 1A. Upload limit to 2 MB

- [x] 1A.1 `lpr_project/settings.py`: change the `UPLOAD_FILE_MAX_SIZE` default from `1048576` to `2097152`; set `FILE_UPLOAD_MAX_MEMORY_SIZE = UPLOAD_FILE_MAX_SIZE` instead of the hard-coded 250 KB; leave `DATA_UPLOAD_MAX_MEMORY_SIZE` unchanged (file parts are excluded from it).
- [x] 1A.2 `.env.example` and `.env.llamacpp.example`: `UPLOAD_FILE_MAX_SIZE=2097152`.
- [x] 1A.3 `docker-compose.yaml`, `docker-compose.traefik.yaml`, `docker-compose-llamacpp-cpu.yml`, `docker-compose-llamacpp-amd-vulcan.yml`: change `${UPLOAD_FILE_MAX_SIZE:-10485760}` to `${UPLOAD_FILE_MAX_SIZE:-2097152}`.
- [x] 1A.4 Fix docs: `AGENTS.md` (currently says "250KB in settings.py (10MB in Docker compose)" — both wrong after this), `README.md` and `DOCKER_DEPLOYMENT.md` tables. Add a CHANGELOG entry flagging the Docker default *reduction* from 10 MB and how to override it.
- [x] 1A.5 Tests: a 1.9 MB image is accepted on `/api/v1/ocr/`; a 2.1 MB image is rejected with `FILE_TOO_LARGE`; `/api/v1/config/` reports `max_upload_bytes: 2097152`; `/api/v1/gate/decide/` rejects a request with more than `GATE_BURST_FRAMES` files before processing any of them.

## 1B. Authentication and roles

- [x] 1B.1 Add `lpr_app/views/auth_views.py`: `POST /api/v1/auth/login/`, `POST /api/v1/auth/logout/`, `GET /api/v1/auth/me/` (user + roles), and a CSRF-cookie bootstrap on `me`.
- [x] 1B.2 Data migration creating `gate_admin` and `gate_operator` groups with the model permissions listed in design.md §11.
- [x] 1B.3 Add view decorators `require_gate_admin`, `require_gate_operator`, `require_agent_token`, `require_device_token` in `lpr_app/utils/`; apply to every gate endpoint.
- [x] 1B.4 Session cookie settings: `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE='Lax'`, `CSRF_COOKIE_SAMESITE='Lax'`; configurable `SESSION_COOKIE_SECURE`.
- [x] 1B.5 Tests: anonymous → 403 on every gate/admin endpoint; operator → 403 on camera endpoints; admin → 200; agent token accepted only on agent endpoints; device token only on heartbeat.

## 2A. Camera configuration

- [x] 2A.1 `lpr_app/utils/secrets.py`: Fernet encrypt/decrypt keyed by `GATE_CONFIG_ENCRYPTION_KEY`; a clear error when the key is missing; add `cryptography` to `requirements.txt`.
- [x] 2A.2 `lpr_app/services/camera_service.py`: vendor presets (Hikvision, Dahua, generic), URL builders for RTSP main/sub and snapshot, credential redaction helper for logs, and CIDR validation of the *resolved* host against `GATE_CAMERA_ALLOWED_CIDRS`.
- [x] 2A.3 Camera CRUD API under `/api/v1/gate/cameras/` (`gate_admin` only). Responses return `password_set`, never the password. A blank password on update keeps the stored one. Every save increments `config_version` and writes a `GateConfigChange`.
- [x] 2A.4 `POST /api/v1/gate/cameras/test/`: accepts a camera id or unsaved form values; runs RTSP TCP reachability and snapshot fetch with Digest→Basic auth; no redirects, 5 s timeout, 5 MB cap, `image/jpeg` only; returns per-step results and the preview JPEG; stores the result on the camera when an id was given.
- [x] 2A.5 `GET /api/v1/gate/agent-config/` (agent token only): returns each enabled gate with its camera config including the decrypted password and `config_version`; honours `If-None-Match` / version and answers `304` when unchanged.
- [x] 2A.6 `GET /api/v1/gate/config-changes/` (`gate_admin` only): paginated audit log.
- [x] 2A.7 Tests: password never appears in any operator/admin response or in logs; blank password keeps the old one; host outside allowed CIDRs rejected at save and at test; hostname resolving outside the CIDRs rejected; redirect not followed; non-JPEG response rejected; `config_version` increments; `304` on unchanged agent-config; audit row written with password masked.

## 2. Decision endpoint

- [x] 2.1 Create `lpr_app/services/gate_service.py` with `decide(gate, frames)`: run each frame through `ImageProcessingService.process_uploaded_image()` (only the winning frame with `save_image=True`), normalise, apply the consensus + confidence rules, match against the registry, write the `AccessEvent`, and return the decision.
- [x] 2.2 Create `lpr_app/views/gate_views.py` with `POST /api/v1/gate/decide/` (multipart, 1–N frames), `POST /api/v1/gate/heartbeat/`, and `POST /api/v1/gate/override/` (authenticated manual open/close/stop by a guard).
- [x] 2.3 Add `GET /api/v1/gate/status/` returning each gate's enabled state, mode, controller reachability and last event — for the SPA status panel.
- [x] 2.4 Wire all routes in `lpr_app/urls.py`.
- [x] 2.5 Add gate settings to `lpr_project/settings.py` via `config()`: `GATE_MODE`, `GATE_BURST_FRAMES`, `GATE_CONSENSUS_MIN`, `GATE_MIN_CONFIDENCE`, `GATE_DECIDE_TIMEOUT`, `GATE_EVENT_RETENTION_DAYS`, `GATE_AUTO_CLOSE`, `GATE_AUTO_CLOSE_SECONDS`, `GATE_AGENT_TOKEN`, `GATE_CONFIG_ENCRYPTION_KEY`, `GATE_CAMERA_ALLOWED_CIDRS`. No camera address or credential settings.
- [x] 2.6 Add a regression test asserting `/api/v1/gate/decide/` is not throttled by `RateLimitMiddleware` under the default `RATE_LIMIT_INCLUDE_PATHS`.
- [x] 2.7 Tests for every decision path: granted, not_registered, expired, inactive, low_confidence, no_consensus, no_plate, shadow_mode, device_disabled, and inference-backend timeout.

## 3. Metrics and retention

- [x] 3.1 Add to `lpr_app/metrics.py`: `lpr_gate_decisions_total{gate,decision,reason}`, `lpr_gate_decision_duration_seconds`, `lpr_gate_controller_up{gate}`, `lpr_gate_commands_total{gate,command,result}`, `lpr_gate_camera_frames_total{gate,result}`.
- [x] 3.2 Add `lpr_app/management/commands/purge_access_events.py` deleting events and their `UploadedImage` rows/files older than `GATE_EVENT_RETENTION_DAYS`.
- [x] 3.3 Register the purge job in `lpr_app/scheduler.py` and `lpr_app/apps.py` alongside `retry_stuck_images` (daily).
- [ ] 3.4 Add a Grafana panel row for gate decisions, grant rate, decision latency, and controller uptime; add an alert on `lpr_gate_controller_up == 0` for 1 m.

## 4. Camera agent

- [x] 4.1 Create `gate-agent/gate_agent.py` following the `canary/canary.py` pattern: only `LPR_API_URL`, `GATE_AGENT_TOKEN` and the metrics port come from env; Prometheus exporter on its own port, main loop, structured logging.
- [x] 4.1a Implement config sync: poll `/api/v1/gate/agent-config/` every 30 s with the held `config_version`; on change, close and reopen the camera with the new settings; on failure keep the last known config; never log the password.
- [x] 4.2 Implement ingest: snapshot-HTTP (Digest→Basic auth) preferred, RTSP fallback; reconnect with backoff on stream loss; report camera state (`streaming` / `reconnecting` / `auth_failed` / `unreachable`) in the status call; emit `lpr_gate_camera_frames_total`.
- [x] 4.3 Implement the presence trigger: ROI frame-differencing on the substream, arrival-then-settle detection, post-decision cooldown.
- [x] 4.4 Implement burst capture and crop to the camera's normalised ROI; send frames at normal JPEG quality, stepping quality down only if a frame exceeds the 2 MB `UPLOAD_FILE_MAX_SIZE` reported by `/api/v1/config/`.
- [x] 4.5 Implement the decide call with `GATE_DECIDE_TIMEOUT`, and the controller call on `granted` (skipped entirely when `GATE_MODE=shadow`).
- [x] 4.6 Implement the controller client: bearer token, `nonce` + `ts`, single retry, and a hard local rate limit as a second line of defence behind the firmware one.
- [x] 4.7 Create `gate-agent/Dockerfile`; add the build to `.github/workflows/docker-publish.yml` so CI publishes it to GHCR alongside the other images.
- [x] 4.8 Add a `gate` profile service to `docker-compose.yaml` referencing the pre-built GHCR image (no `build:` directive).
- [x] 4.9 Add an offline replay mode (`--replay <dir>`) that feeds recorded frames instead of the camera, so the agent is testable without site access. Implemented as `python -m gate_agent replay` (virtual time at the recorded fps) plus `python -m gate_agent decide` for single bursts of photos.
- [x] 4.10 Simulated barrier (design.md §13): `SimulatedBarrier` model, `barrier_simulator` service implementing the controller contract, `/api/v1/gate/sim/<id>/<command>`, `controller_type` on gates, `can_actuate` dispatch rule, `/api/v1/gate/devices/<id>/test-decide/` and `/simulator/`, Django admin "Test recognition" page, `is_test` events, `lpr_gate_arm_up`.
- [x] 4.11 End-to-end test: the real agent against a live test server and the simulated barrier (`lpr_app/tests/test_gate_agent_e2e.py`).
- [x] 4.12 Camera "Test connection" action in Django admin.
- [ ] 4.13 Verify the `open-lpr-gate-agent` image builds in CI for amd64 and arm64 (`opencv-python-headless` on `python:3.11-slim`), and that the container reaches the camera at `172.87.80.80` from the compose network.

## 5. ESP32 controller

- [ ] 5.1 Create `gate-controller/` with a PlatformIO/Arduino sketch: WiFi with reconnect, HTTP server, bearer-token auth, nonce/timestamp replay rejection.
- [ ] 5.2 Implement `/open`, `/close`, `/stop`, `/status` as momentary `PULSE_MS` pulses on the three relay GPIOs, all relays idle open.
- [ ] 5.3 Implement the safety invariants: UP/DOWN `INTERLOCK_MS` interlock, `MIN_COMMAND_INTERVAL_MS` rate limit (exempting `/stop`), hardware watchdog, idle state on boot and on WiFi loss.
- [ ] 5.4 Implement the 10 s heartbeat POST to `/api/v1/gate/heartbeat/`.
- [ ] 5.4a If 0.4e shows dry contacts: read `UP LIMIT OUTPUT` and `DOWN LIMIT OUTPUT` on two GPIOs (internal pull-up, contact to ESP32 GND), derive `arm_state` (`up`/`down`/`moving`/`unknown`), include it in the heartbeat and in `/status`, and report an `open` as confirmed only when the up limit is reached within a timeout.
- [x] 5.4b Django: add `GateDevice.arm_state` + `arm_state_at`, accept `arm_state` in the heartbeat, show it in `/api/v1/gate/status/`, add `lpr_gate_arm_up{gate}` gauge and an alert for "arm up longer than N minutes".
- [ ] 5.5 Write `gate-controller/README.md` (for the `BR6_CT`: relays in parallel with the existing UTP pairs at the controller, `STOP` wiring per 0.4b, never touch `L`/`N`/`E`): GPIO-to-terminal wiring table (relay NO contacts bridging `COM`↔`UP`/`DOWN`/`STOP`), opto-isolated module, separate 5 V coil supply with commoned grounds, guard push-buttons left in parallel, flashing and provisioning steps.
- [ ] 5.6 **Bench test against LEDs, not the barrier**: verify pulse width on a scope or logic analyser, verify the interlock cannot be defeated over HTTP, verify power-cycle and WiFi-loss both land in the all-open state.

## 6. Operator and admin UI

- [x] 6.0 Add a `/login` page, an auth context that calls `/api/v1/auth/me/` on load, CSRF header on unsafe requests, route guards by role, and a user menu with logout in the `Navbar`. Admin-only nav items are hidden from operators (the API still enforces it).
- [x] 6.0a Add `/manage/cameras` (list) and `/manage/cameras/[id]` (edit) for `gate_admin` (`/manage`, not `/admin`: behind the shared reverse proxy `/admin/` belongs to the Django admin, which hosts the gate "Test recognition" page):
  - form fields: name, host/IP, RTSP port, HTTP port, username, password (empty = keep current, with a "password set" indicator), vendor dropdown that pre-fills the three paths, editable paths, "prefer snapshot" toggle, trigger tuning under an "Advanced" disclosure;
  - inline validation for IP/hostname and port range, and a clear message when the host is outside the allowed network ranges;
  - **Test connection** button showing per-step results (port reachable / snapshot authenticated / image received) and the preview snapshot;
  - **ROI picker**: drag a rectangle on the preview to set the read zone, with a reset button;
  - live agent status for this camera (`streaming`, `reconnecting`, `auth_failed`, …) and last test time/result;
  - change history panel from `/api/v1/gate/config-changes/`.
- [x] 6.0b Add `/manage/gates` for `gate_admin`: gate devices, their assigned camera, controller URL, mode, enabled flag, `has_safety_input`.
- [x] 6.1 Add `/vehicles` to `single-page-ui`: list, search by plate, create/edit/deactivate, with plate normalisation previewed live as the user types.
- [x] 6.2 Add `/events`: paginated access-event log with decision/reason filters, the captured frame thumbnail, and a one-click "open anyway" override for near-miss and denied events.
- [x] 6.3 Add a gate status panel (`/gate`) (mode, controller online, last decision) and an emergency STOP control.
- [x] 6.4 Add `single-page-ui/src/lib/gate-api.ts` (session calls next to `api.ts`) with the new types and calls (with `credentials: 'include'` and the CSRF header); add mock data and Storybook stories for the new components — including the camera form in its empty, saved, testing, test-failed and ROI-editing states — matching the existing pattern. Mock data lives in `src/lib/gate-mock-data.ts`.
- [x] 6.5a Add `/monitor`: live lane view per gate (snapshot proxy endpoint, read zone overlay, refresh interval, pause), camera/controller status, arm state and a recent-decision ticker.
- [x] 6.5 Browser end-to-end run of the SPA against a live backend, the real gate agent and a stand-in camera: login and role guards, CSRF across origins, camera validation/allowlist/test/ROI/history, gate and vehicle CRUD, manual open/STOP through the agent to the simulated arm, event filters, phone width.

## 7. Configuration and documentation

- [x] 7.1 Add every new variable to `.env.example` and `.env.llamacpp.example` with safe defaults (`GATE_MODE=shadow`).
- [x] 7.2 Document all new variables in `AGENTS.md` under Environment Variables, and the `gate` profile under Docker.
- [ ] 7.3 Write `GATE_DEPLOYMENT.md`: site survey checklist, wiring diagram, provisioning, the staged rollout, runbook for "barrier will not open", and the rollback procedure.
- [ ] 7.4 Note in the README that the gate feature is on-premise only and ships disabled.

## 8. Staged rollout

- [ ] 8.0 **Setup.** Generate `GATE_CONFIG_ENCRYPTION_KEY` and `GATE_AGENT_TOKEN` into the gate host `.env` and back them up. Create the `gate_admin` and `gate_operator` accounts. Once the addressing question (0.1) is resolved, add the camera's range to `GATE_CAMERA_ALLOWED_CIDRS` if it is outside RFC 1918. In `/manage/cameras`, enter the camera (host, `admin`, the rotated password, vendor), press **Test connection** until all steps pass, draw the ROI on the preview, save, and confirm the agent status turns `streaming`.
- [ ] 8.1 **Stage A — shadow.** Deploy agent + Django with `GATE_MODE=shadow` and no ESP32 connected. Run over real traffic for at least one week. Seed the registry with the real authorised list.
- [ ] 8.2 Review the shadow event log: zero grants that should not have been grants; measure the grant rate on registered vehicles and the p95 decision latency; tune camera aim, ROI, confidence threshold and burst size. Do not advance until the false-grant count is zero.
- [ ] 8.3 **Stage B — bench.** ESP32 flashed and wired to LEDs on the bench, agent in `live` mode pointed at it. Verify every command path and every safety invariant end to end.
- [ ] 8.4 **Stage C — supervised live.** With electrical sign-off, wire the relays to the barrier terminals. Guard present at all times, hand on the manual buttons, for a full working week. Log every manual intervention and why.
- [ ] 8.5 **Stage D — unsupervised.** Only after a clean supervised week and with a tested safety sensor on the controller (0.4f), `has_safety_input=True`. Grafana alerts active; the guard retains full manual control and the documented rollback.
- [ ] 8.6 Post-rollout review at 30 days: grant rate, false denies, storage growth, retention job health.

## 9. Verification

- [ ] 9.1 `python manage.py test` passes.
- [ ] 9.2 `coverage run --source='lpr_app' manage.py test && coverage report` shows ≥ 80% on all new and changed `lpr_app` files.
- [ ] 9.3 The agent has unit tests for normalisation-independent logic (trigger, burst, controller client) runnable without a camera, via replay mode.
