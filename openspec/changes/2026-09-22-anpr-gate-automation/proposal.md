## Why

The VietinBank training school ("VietinBk") gate is operated manually: a guard identifies each arriving vehicle and presses the barrier controller's UP button. This project already contains everything needed to read a plate from an image — a two-phase Qwen3-VL detection + OCR pipeline behind a synchronous `POST /api/v1/ocr/` endpoint, image storage, Prometheus metrics, an APScheduler runner, and a Next.js operator UI. What is missing is everything *around* the recognition: a live camera feed, a registry of plates that are allowed through, a decision rule, a physical actuator, and an audit trail.

This change turns the existing LPR service into an access-control system for one gate, driven by a registry of authorised plates managed in the software, a fixed IP camera, and an ESP32 relay board wired to the barrier controller's dry-contact terminals.

## What Changes

- **Vehicle registry** — new `Vehicle` model plus CRUD API and SPA screens holding the authorised plates (normalised plate number, owner, department, vehicle type, validity window, active flag).
- **Gate devices** — new `GateDevice` model describing each gate: its camera (FK to `Camera`), ESP32 controller URL, shared secret, direction (in/out), enabled flag, heartbeat timestamp.
- **Camera configuration in the admin UI** — new `Camera` model and an admin-only `/manage/cameras` page in the SPA where the local camera's address is set and changed without touching env files or redeploying: host/IP, RTSP and HTTP ports, username, password (write-only, encrypted at rest), vendor preset (Hikvision / Dahua / generic) that fills in stream and snapshot paths, region of interest, and trigger tuning. Includes a **Test connection** button returning a live snapshot, a drag-to-draw ROI picker on that snapshot, and an audit trail of every change. The gate agent picks up saved changes within 30 s without a restart.
- **Admin authentication** — the SPA and API have no authentication today. This change adds Django session login for the SPA with two roles: `gate_admin` (cameras, gate devices, everything below) and `gate_operator` (vehicle registry, event log, manual override). All gate management endpoints require one of them; the camera agent and controller use their own tokens.
- **Upload limit raised to 2 MB** — `UPLOAD_FILE_MAX_SIZE` defaults to 2 MB (2097152) everywhere: `settings.py` (currently 1 MB), both `.env` examples (currently 1 MB), and every compose file (currently 10 MB). All components run on the internal network, so full-resolution camera frames can be sent without aggressive re-compression. The SPA already reads the limit from `/api/v1/config/` and follows automatically.
- **Decision endpoint** — new `POST /api/v1/gate/decide/` that accepts a frame (or a set of frames) from the camera agent, runs it through the existing `ImageProcessingService`, normalises the OCR text, matches it against the registry, and returns `granted` / `denied` with a machine-readable reason. It never talks to the hardware itself.
- **Camera agent** — new `gate-agent/` service (same shape as the existing `canary/` service: standalone Python, own Dockerfile, own compose profile) that pulls frames from the IP camera, detects vehicle presence, submits a short burst of frames for a consensus read, and — only on `granted` — pulses the ESP32's UP relay.
- **ESP32 controller contract** — an HTTP command protocol (`/open`, `/close`, `/stop`, `/status`) over the gate LAN, token-authenticated, with momentary relay pulses, a UP/DOWN interlock, a command watchdog, and a heartbeat back to Django. Firmware lives in `gate-controller/` as an Arduino/PlatformIO sketch.
- **Access audit** — new `AccessEvent` model recording every decision (plate read, confidence, matched vehicle, decision, reason, linked `UploadedImage`, command result, latency), exposed read-only via API and an SPA event log, with a retention purge job on the existing scheduler.
- **Shadow mode** — a `GATE_MODE=shadow|live` switch. In `shadow`, every decision is computed and logged but no relay command is ever sent. This is the default and the required first stage of rollout.
- **Metrics** — new Prometheus series for decisions, read latency, controller reachability, and camera frame health, surfaced on the existing `/metrics/` endpoint and Grafana stack.

## Capabilities

### New Capabilities
- `gate-vehicle-registry`: authorised-plate registry, plate normalisation, and matching rules
- `gate-access-decision`: the decision endpoint contract — inputs, consensus rules, decisions and reasons
- `gate-camera-agent`: camera ingest, presence trigger, burst capture, and actuation dispatch
- `gate-controller-integration`: the ESP32 command protocol, safety interlocks, and heartbeat
- `gate-access-audit`: the access event record, its API, and retention
- `gate-camera-config`: admin-managed camera connection settings, credential handling, connection test, ROI, and hot reload into the agent
- `admin-auth`: session login for the SPA and role-based access to gate management endpoints
- `api-upload-limits`: the upload size limit, its 2 MB default, and how it applies to multi-frame gate requests

### Modified Capabilities
- `api-rate-limiting`: gate endpoints are exempt from the per-IP throttle that protects `/api/v1/ocr/`

## Impact

- **New Django code**: `lpr_app/models.py` (`Vehicle`, `Camera`, `GateDevice`, `AccessEvent`, `GateConfigChange` + migration), `lpr_app/services/gate_service.py`, `lpr_app/services/plate_matcher.py`, `lpr_app/services/camera_service.py`, `lpr_app/utils/secrets.py`, `lpr_app/views/gate_views.py`, `lpr_app/views/auth_views.py`, `lpr_app/urls.py`, `lpr_app/metrics.py`, `lpr_app/admin.py`, `lpr_app/management/commands/purge_access_events.py`, `lpr_app/scheduler.py`
- **New dependency**: `cryptography` (Fernet encryption for the stored camera password)
- **New services**: `gate-agent/` (Python + Dockerfile), `gate-controller/` (ESP32 firmware, not built by CI)
- **Frontend**: login page and session handling; `/vehicles`, `/events`, `/manage/cameras` routes; a live gate status panel and a guard override control in `single-page-ui/`
- **Compose**: new `gate` profile in `docker-compose.yaml` referencing a new GHCR image built by CI; `UPLOAD_FILE_MAX_SIZE` default changed from 10 MB to 2 MB in `docker-compose.yaml`, `docker-compose.traefik.yaml` and the two deprecated llamacpp compose files
- **Upload limit**: `lpr_project/settings.py` default 1 MB → 2 MB, `FILE_UPLOAD_MAX_MEMORY_SIZE` tied to it; `.env.example`, `.env.llamacpp.example`, `AGENTS.md` (which currently misstates the default as 250 KB), `README.md` and `DOCKER_DEPLOYMENT.md` updated
- **Config**: ~18 new environment variables in `.env.example`, `.env.llamacpp.example`, and `AGENTS.md`. Camera address and credentials are **not** among them — they live in the database and are managed from the admin UI
- **Deployment**: on-premise host at the gate running the `core` + an inference profile; no internet dependency at runtime
- **Physical**: low-voltage dry-contact wiring into an existing barrier controller — requires site electrical sign-off before Stage 4

## Non-Goals

- Exit-lane automation and vehicle counting/occupancy (one entry gate first; the models are built to take a second `GateDevice` later)
- Face recognition, driver identification, or any biometric processing
- Replacing the guard. The manual UP/DOWN/STOP buttons stay wired and always win.
- Barrier auto-close driven by software on a lane with no safety loop detector (see design.md, "Closing the barrier")
