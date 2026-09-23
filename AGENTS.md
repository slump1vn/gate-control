# AGENTS.md

## Project Overview

Django 5.2 LTS web app for license plate recognition using Qwen3-VL vision-language model via an OpenAI-compatible API. Python 3.10+, SQLite by default.

## Commands

```bash
python manage.py runserver              # Dev server (port 8000)
python manage.py test                   # Django test runner (no formal test suite exists yet)
python manage.py makemigrations lpr_app # Create migrations after model changes
python manage.py migrate                # Apply migrations
python manage.py collectstatic --noinput # Collect static files (required before Docker deploy)
python test_api.py /path/to/image.jpg   # Manual API integration test (requires running server)
```

There is no linter, formatter, or typecheck configured.

## Architecture

- **`lpr_project/`** — Django project config (`settings.py`, `urls.py`, `wsgi.py`)
- **`lpr_app/`** — The sole Django app containing all business logic
  - `models.py` — `UploadedImage`, `ProcessingLog`; gate: `Vehicle`, `Camera`, `GateDevice`, `AccessEvent`, `GateConfigChange`
  - `views/` — API-only view subpackage: `api_views.py`, `file_views.py`, `auth_views.py`, `gate_views.py`, `gate_admin_views.py`
  - `services/` — Business logic layer
    - `qwen_client.py` — OpenAI-compatible client for Qwen3-VL, prompt templates, coordinate conversion
    - `image_processor.py` / `image_processing_service.py` — Image handling
    - `bbox_visualizer.py` — Bounding box drawing
    - `api_service.py`, `file_service.py` — Service layer
  - `utils/` — Helpers: `validators.py`, `response_helpers.py`, `metrics_helpers.py`
  - `management/commands/` — `setup_project`, `inspect_image`, `retry_stuck_images`, `purge_access_events`, `generate_gate_secrets`
  - Gate automation: `services/gate_service.py` (decisions, command queue), `services/plate_matcher.py` + `utils/plates.py` (normalisation, registry matching), `services/camera_service.py` (presets, allowlist, connection test), `services/config_audit.py`, `views/gate_views.py` (agent/controller/operator runtime), `views/gate_admin_views.py` (cameras, devices, vehicles, events), `views/auth_views.py` (session login), `utils/auth.py` (roles `gate_admin`/`gate_operator`, agent/device tokens)
- **`single-page-ui/`** — Next.js 16 SPA frontend (React 19, Tailwind CSS 4, Storybook 10)
  - Gate pages: `/login`, `/gate` (status, STOP), `/vehicles`, `/events` (operators); `/manage/cameras`, `/manage/gates` (admins). Admin pages live under `/manage`, never `/admin`: `/admin/` is the Django admin
  - `src/lib/gate-api.ts` — session-authenticated calls (`credentials: 'include'`, `X-CSRFToken` from the `/api/v1/auth/` response body); `src/components/AuthContext.tsx` + `RequireRole.tsx` guard pages client-side only, the API enforces roles
  - Tests: `npx vitest run` runs every Storybook story in headless Chromium (needs `npx playwright install chromium` once)
- **`canary/`** — Separate canary monitoring service (its own Dockerfile)
- **`gate-agent/`** — Gate camera agent (own package, Dockerfile, CI workflow `gate-agent-publish.yml`, image `open-lpr-gate-agent`). Tests: `cd gate-agent && python -m unittest discover -s tests -t .`. See `gate-agent/README.md`
- **`blackbox/`** — Blackbox exporter config for Prometheus probing

## Key Patterns & Gotchas

- Static files are served by WhiteNoise (`whitenoise.middleware.WhiteNoiseMiddleware`, `CompressedStaticFilesStorage`). Django itself only serves `/static/` and `/media/` when `DEBUG=True`, so production needs it for the admin's CSS/JS. `collectstatic` runs in `docker-entrypoint.sh`
- Settings use `python-decouple` (`config()` calls in `settings.py`), not raw `os.environ`. Env vars are loaded from `.env` or `.env.llamacpp`.
- Two env file modes: `.env` for external API, `.env.llamacpp` for bundled LlamaCpp inference. Docker Compose reads `.env.llamacpp` by default.
- The AI client (`QwenVLClient`) wraps the `openai` Python SDK. It calls any OpenAI-compatible endpoint (LlamaCpp, vLLM, remote API).
- Detection uses a two-phase pipeline: Phase 1 detects plate bounding boxes, Phase 2 runs OCR on cropped regions. Prompts are in `qwen_client.py`.
- Bounding box coordinates arrive in Qwen2VL 0-1000 normalized range and must be converted via `convert_from_qwen2vl_format()`. Because they are normalized, upscaling a crop before sending it does not affect the mapping back to the original image.
- `UploadedImage` media is organized into `uploads/YYYY/MM/DD/` and `processed/YYYY/MM/DD/` subdirectories.
- Django serves API-only (no templates, no web UI). The frontend is a separate Next.js SPA in `single-page-ui/`. The one exception is Django admin extensions for operators and installers: the gate "Test recognition" page (`lpr_app/templates/admin/lpr_app/gatedevice/test_gate.html`). Do not add user-facing Django templates.
- `upload_to` path helpers in `models.py` generate date-partitioned upload paths.

## Docker

Profile-based Docker Compose (deprecated individual compose files must not be used):

```bash
docker compose --profile core --profile cpu up -d          # CPU inference
docker compose --profile core --profile nvidia-cuda up -d  # NVIDIA GPU
docker compose --profile core --profile amd-vulkan up -d   # AMD Vulkan GPU
docker compose --profile core up -d                        # External API only
docker compose --profile core --profile cpu --profile gate up -d  # + gate camera agent
```

- Images published to `ghcr.io/slump1vn/gate-control` (app), `-spa`, `-gate-agent`, `-canary`, `-prometheus`, `-grafana`, `-blackbox`. Compose reads `LPR_IMAGE_PREFIX` (default `ghcr.io/slump1vn/gate-control`), so a different registry or owner needs no file edit
- **All Docker images are built and published by GitHub Actions CI.** Docker Compose files (`docker-compose.yaml`) only reference pre-built images from GHCR — never use `build:` directives in compose files.
- CI: `.github/workflows/docker-publish.yml` builds multi-arch (amd64/arm64) on push to main and version tags
- Container runs as `django` user via `gosu` (see `docker-entrypoint.sh`)
- `lpr-app` runs one gunicorn process with 8 threads: one process keeps a single APScheduler instance, and the threads keep a slow gate decision (up to `GATE_DECIDE_TIMEOUT`) from blocking every other request, including a manual STOP
- Grafana publishes on `GRAFANA_PORT` (default `3001`) because the SPA uses 3000
- `docker-entrypoint.sh` runs migrate + collectstatic + optional createsuperuser on every start
- `fonts-noto` and `fonts-noto-cjk` are installed in the Docker image for Unicode text rendering (Arabic, CJK, etc.) on bounding box visualizations

## Environment Variables

Key variables (see `.env.example` and `.env.llamacpp.example` for full list):

- `QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_MODEL` — AI model connection
- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` — Django core
- `CORS_ALLOWED_ORIGINS` — Comma-separated frontend origins allowed to access the API (default: `http://localhost:3000`)
- `CORS_ALLOW_PRIVATE_NETWORK` — Allow browsers to access the API from a public origin when the API resolves to a private IP (default: `False`). Set to `True` when using Cloudflare-proxied frontend with a local API endpoint.
- `RATE_LIMIT_ENABLE` — Enable per-IP rate limiting on API endpoints (default: `True`)
- `RATE_LIMIT_RATE` — Throttle rate in `num/period` format, e.g. `2/min` (default: `2/min`)
- `RATE_LIMIT_EXCLUDE_PATHS` — Comma-separated URL paths excluded from rate limiting (default: `/health/,/api/v1/health-light/`)
- `RATE_LIMIT_INCLUDE_PATHS` — Comma-separated URL paths to rate limit; all other paths are exempt (default: `/api/v1/ocr/`)
- `OCR_CROP_MIN_WIDTH` — Plate crops narrower than this are upscaled (LANCZOS, at most 4x) before the OCR phase; `0` disables (default: `480`). A plate only ~120px wide gives the model too few pixels per character and digits get confused
- `TIME_ZONE` — Server timezone for Django admin timestamps and the nightly purge job (default: `Asia/Ho_Chi_Minh`). The SPA shows times in the viewer's own timezone
- `LPR_IMAGE_PREFIX` — Image prefix used by all Compose files (default: `ghcr.io/slump1vn/gate-control`)
- `DATABASE_PATH` — SQLite path (default: project root `db.sqlite3`)
- `MEDIA_PATH` — Media storage (default: `./media`, Docker: `./container-media`)
- `UPLOAD_FILE_MAX_SIZE` — Maximum size per uploaded file in bytes (default: `2097152` = 2MB, same in settings.py, env examples and Docker compose). Also sets `FILE_UPLOAD_MAX_MEMORY_SIZE` so accepted uploads stay in memory

### Admin login

- `CSRF_TRUSTED_ORIGINS` — Origins allowed to make session-authenticated unsafe requests (default: same as `CORS_ALLOWED_ORIGINS`). CORS sends credentials for `CORS_ALLOWED_ORIGINS`, so the SPA can log in from another origin of the same site (e.g. a different port)
- `SESSION_COOKIE_SECURE` — Mark session and CSRF cookies Secure; set `True` behind HTTPS (default: `False`)

### Gate automation

Barrier control from plate recognition (see `openspec/changes/2026-09-22-anpr-gate-automation/`). Camera address and credentials are **not** env vars — they are stored (encrypted) in the database and managed from the admin UI / Django admin.

- `GATE_MODE` — `shadow` (decide and log, never actuate) or `live` (default: `shadow`; any other value is treated as shadow)
- `GATE_AGENT_TOKEN` — Bearer token for the gate agent's endpoints; empty rejects all agent calls (default: empty)
- `GATE_CONFIG_ENCRYPTION_KEY` — Fernet key encrypting camera passwords and controller tokens at rest; back it up (default: empty — secrets cannot be saved). Generate both secrets with `python manage.py generate_gate_secrets`
- `GATE_CAMERA_ALLOWED_CIDRS` — Networks camera hosts must resolve into, checked on save and before the connection test connects (default: `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`)
- `GATE_BURST_FRAMES` — Max frames per decision request (default: `3`)
- `GATE_CONSENSUS_MIN` — Frames that must agree on a plate before it can be granted (default: `2`)
- `GATE_MIN_CONFIDENCE` — Minimum OCR confidence among the agreeing frames (default: `0.80`)
- `GATE_DECIDE_TIMEOUT` — Seconds a decision may take before it is denied as `inference_timeout` (default: `8`)
- `GATE_WORKER_THREADS` — Frames recognised in parallel per Django worker process (default: `3`)
- `GATE_COMMAND_TTL_SECONDS` — Manual commands not picked up by the agent within this time expire and are never sent (default: `15`)
- `GATE_HEARTBEAT_TIMEOUT_SECONDS` — Controller is shown offline after this long without a heartbeat (default: `30`)
- `GATE_EVENT_RETENTION_DAYS` — Access events and their frames are purged daily after this many days (default: `90`)
- `GATE_AUTO_CLOSE` — `controller` (barrier's own timer) or `software`; `software` is refused for gates without `has_safety_input` (default: `controller`)
- `GATE_AUTO_CLOSE_SECONDS` — Delay before a software close (default: `10`)

Gate gotchas:
- Gate frames are `UploadedImage` rows with `source='gate'`. They are excluded from the public image list/detail/download endpoints and from `retry_stuck_images`; serve them only via `/api/v1/gate/events/<id>/image/<type>/` (operator login).
- The OpenAI client has no request timeout of its own (SDK default 600s). The gate decision bounds it with `GATE_DECIDE_TIMEOUT` in `gate_service.read_frames`; frames that finish late are deleted.
- Django never contacts an ESP32 controller. The gate agent relays every command, including manual overrides (queued as `AccessEvent`s and claimed via `/api/v1/gate/agent-commands/`).
- A gate with `controller_type='simulator'` is driven by `services/barrier_simulator.py`, served at `/api/v1/gate/sim/<id>/<command>` with the exact ESP32 contract. Simulated gates receive commands even in shadow mode (`gate_service.can_actuate`): shadow forbids *physical* actuation only. The Django admin page `/admin/lpr_app/gatedevice/<id>/test/` runs uploaded photos through the real decision pipeline (`is_test=True`, excluded from metrics) and animates the simulated arm.
- Gate agent env vars (`LPR_API_URL`, `GATE_AGENT_TOKEN`, `AGENT_*`) are documented in `gate-agent/README.md`; `GATE_AGENT_METRICS_PORT` sets the published metrics port in compose (default `9101`).

### SPA Frontend (runtime via Docker environment)

- `BACKEND_API_URL` — Backend API URL (default: empty = relative paths, works behind shared reverse proxy). Docker Compose default: `http://lpr-app:8000`
- `NEXT_PUBLIC_UPLOAD_TIMEOUT` — Upload timeout in ms (default: 120000)

## Deployment

When the user asks to "deploy the change", follow these steps in order:

### 1. Ensure 80%+ test coverage
- Write or update tests to cover the changed code
- Run `python manage.py test` and verify all tests pass
- Ensure coverage is 80%+ for changed files (use `coverage run --source='lpr_app' manage.py test && coverage report`)

### 2. Commit and push via SSH
- Stage only the intended files (never commit secrets)
- Commit with a concise message matching the repo style
- Push to `origin` (git@github.com:faisalthaheem/open-lpr.git) via SSH

### 3. Wait for GitHub Actions CI
- Monitor the workflow run triggered by the push:
  ```bash
  gh run list --limit 1                          # Get latest run ID
  gh run watch <run-id>                          # Stream logs
  ```
- The CI builds multi-arch Docker images and publishes to `ghcr.io/faisalthaheem/open-lpr`
- If the build fails, read the logs with `gh run view <run-id> --log-failed`, fix errors, commit and push again

### 4. Pull latest images on prod server
- SSH to prod: `ssh root@10.1.200.101`
- Navigate to the Coolify app directory and pull the latest images:
  ```bash
  ssh root@10.1.200.101 "cd /path/to/app && docker compose pull"
  ```
- If the exact path is unknown, inspect running containers first:
  ```bash
  ssh root@10.1.200.101 "docker ps --format '{{.Names}} {{.Image}}'"
  ```

### 5. Notify user
- Tell the user the images are pulled and ready
- The user will redeploy the app via Coolify themselves

## Conventions

- **Environment variables**: When adding new environment variables, add them to `.env.example`, `.env.llamacpp.example`, and any relevant Docker Compose files. Document them in this file under Environment Variables.
- **Docker images**: All images are built and published by GitHub Actions CI. Never add `build:` directives to Docker Compose files — always reference pre-built images from GHCR.
