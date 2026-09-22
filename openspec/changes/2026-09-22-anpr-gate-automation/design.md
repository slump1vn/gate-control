## Context

The existing system is a request/response OCR service: a client POSTs an image to `/api/v1/ocr/`, `ImageProcessingService.process_uploaded_image()` runs Phase 1 (downscale → plate detection) and Phase 2 (crop → batch OCR) against a Qwen3-VL endpoint, writes an `UploadedImage` row plus a visualised `processed_image`, and returns the merged detections synchronously. There is no notion of *who* a plate belongs to, no camera input, and no output other than JSON.

The gate adds three things the current architecture does not have:

1. A **continuous input** (an RTSP camera) instead of a user-initiated upload.
2. A **decision** with a physical consequence — a wrong `granted` lets an unauthorised vehicle onto the site, so the matching rule must be strict and every decision must be auditable.
3. An **actuator** on the LAN that must fail safe.

Site facts:

- One IP camera at `172.87.80.80` (user `admin`, factory-style password supplied by the site — not recorded here). The site confirmed this is an **internal** address, even though it is outside RFC 1918 space (see §10 and Risks).
- The barrier controller is an AC-motor control board silkscreened **`BR6_CT VER:001`**, supplied under an **APS** label, with MCU sticker `230306`. It exposes `COM`, `UP`, `DOWN`, `STOP` command terminals and is to be driven by an ESP32 with a 3-channel relay board. The board's full terminal map, from site photos, is in §6a.

## Goals / Non-Goals

**Goals**
- Open the barrier automatically, within ~3 s of a vehicle stopping at the read point, for any plate in the registry.
- Never open for a plate that is not in the registry, including under OCR error.
- Keep a complete, queryable audit trail of every read and every decision, with the frame attached.
- Run entirely on-premise with no internet dependency at runtime.
- Degrade to today's manual operation on any failure, with zero extra steps for the guard.

**Non-Goals**
- Sub-second recognition. The VLM pipeline is seconds-scale; the lane geometry, not the model, absorbs that.
- Recognising plates on moving vehicles at speed. Vehicles stop at the barrier; we read from a stopped or crawling vehicle.
- Multi-tenant or multi-site management.

## Decisions

### 1. Split the work: Django decides, the agent acts

The decision ("is this plate allowed?") lives in Django, where the registry, the audit log and the operator UI already are. The actuation ("pulse the UP relay") lives in the `gate-agent` process at the gate. Django never opens a TCP connection to the ESP32.

This keeps the barrier reachable only from a process on the gate LAN, means a compromised or misconfigured Django instance cannot actuate hardware directly, and lets the agent keep working through a Django restart (it simply denies, which is the safe outcome).

The agent is modelled on the existing `canary/` service — a single Python file, its own `Dockerfile`, its own compose profile, talking to the API over HTTP. That pattern is already proven in this repo and in CI.

**Alternative considered**: Django calling the ESP32 directly on each grant. Rejected — it couples the web tier to gate hardware, needs Django to reach the gate VLAN, and gives a worse failure mode (a hung HTTP call inside a request handler).

**Alternative considered**: MQTT broker between agent and controller. Rejected for stage 1 — it adds a broker to operate for a single device on a single LAN. The controller contract is written so MQTT can be added later as a second transport without changing the decision layer.

### 2. Camera ingest: snapshot-first, RTSP as the trigger

Hikvision/Dahua-class cameras expose both an RTSP stream and a still-snapshot HTTP endpoint (`/ISAPI/Streaming/channels/101/picture` or `/cgi-bin/snapshot.cgi`). The agent probes for a snapshot endpoint at startup and prefers it: a JPEG snapshot needs no decoder, no ffmpeg/OpenCV in the image, and yields a full-resolution frame.

RTSP (via OpenCV) is the fallback and is also what drives the **presence trigger**: a low-resolution substream (channel 102) is decoded at ~5 fps and a frame-difference score over a configured region of interest tells us a vehicle has arrived and settled. We do not run the VLM on every frame — a burst is triggered when the difference score rises above a threshold and then falls back to a plateau (vehicle stopped), with a cooldown after each decision.

Running the VLM continuously would cost ~1 inference/second forever; the trigger reduces that to a handful per vehicle.

**Frame preparation.** With `UPLOAD_FILE_MAX_SIZE` at 2 MB (§9), a 1080p JPEG snapshot fits as-is and a 4 MP frame fits at normal quality, so the agent does not need to degrade the image. It still crops to the configured ROI — not for size, but because a tighter crop makes the plate a larger fraction of the image that Phase 1 downscales, which is what keeps the plate at least `MIN_PLATE_HEIGHT` (30 px) tall after downscaling. Quality is only stepped down as a fallback for oversized frames. Plate height at the read point is a camera-aiming constraint as much as a software one; see "Site survey" in tasks.md.

Camera address, credentials, stream paths and ROI are not environment variables: they come from the `Camera` record managed in the admin UI (§10).

### 3. Consensus read, not a single read

A single VLM read is not trustworthy enough to move a barrier. The agent captures a burst of `GATE_BURST_FRAMES` (default 3) frames ~400 ms apart and submits them in one `/api/v1/gate/decide/` request. The server reads each, normalises each result, and requires:

- at least `GATE_CONSENSUS_MIN` (default 2) frames to yield the **same normalised plate**, and
- the best confidence among the agreeing frames to be ≥ `GATE_MIN_CONFIDENCE` (default 0.80), and
- an **exact** match of that normalised plate against an active registry entry.

Anything less is `denied`, with a reason that distinguishes "no plate found", "no consensus", "low confidence", "not registered" and "registration expired" — so the event log tells the operator whether to re-aim the camera or add a vehicle.

**Cost note**: 3 frames × 2 VLM phases is 6 model calls per vehicle. At a training school's traffic volume this is fine on a single local GPU; if it proves too slow at peak, `GATE_BURST_FRAMES=2` with `GATE_CONSENSUS_MIN=2` is the tighter, slower-to-grant fallback.

### 4. Plate normalisation is positional, and fuzzy matching never opens the barrier

Vietnamese plates have a fixed shape: two province digits, one or two series characters (a letter, or a letter plus a digit on motorbikes), then four or five digits — e.g. `30A-123.45`, `29X1-234.56`. Normalisation strips separators and uppercases, then applies **positional coercion**: at positions that must be digits, a character the model confuses with a digit is corrected (`O/D/Q→0`, `I/L/T→1`, `Z→2`, `S→5`, `G→6`, `B→8`, `A→4`); at the series position, the reverse mapping applies. `30A-123.45` and `3OA—12345` both normalise to `30A12345`.

Position 3 is a digit on most plates (`30A1…`, `29X1…`) but a letter on two-letter series (`51LD…`). It is coerced only when the character is a digit-confusable, so `51LD12345` normalises to `51L012345`. That looks odd, but it is harmless: the same function normalises the registry entry and the OCR read, so they still match exactly. What the scheme must guarantee is consistency and no collisions between real plates, and the unique constraint on `plate_normalized` enforces the second. Strings that do not fit a 7–9 character plate layout are only cleaned, not coerced.

Positional coercion is deterministic and information-preserving in a way generic edit-distance matching is not. After it, matching against the registry is an **exact** lookup on an indexed column.

A near-miss (edit distance 1 against exactly one active registry entry after normalisation) is recorded on the event as `near_miss_vehicle` and surfaced in the UI so the guard can open manually with one click — but it is **never** auto-granted. This is the central safety trade-off: we accept occasional manual intervention to make an unauthorised auto-open essentially impossible.

### 5. Reuse `UploadedImage` for the audit frame; store one frame per event

`AccessEvent.uploaded_image` is a nullable FK to the existing `UploadedImage`, so every gate frame goes through the unchanged pipeline and gets its bounding-box visualisation. Every frame in a burst is processed with `save_image=True`. The canary's `save_image=False` path cannot be reused, because it deletes the record, and the detections with it, before the caller can read them. Once the decision is made, only the frame behind it (the best agreeing read, or the first read when nothing agreed) is kept as evidence. `gate_service.discard_frame` deletes the other frames' original, processed and comparison files and their records.

**Gate frames are not public.** The existing image list, detail and download endpoints are anonymous, and downloads use sequential integer ids. Exposing gate frames there would publish every staff vehicle's photo and plate. `UploadedImage` gains `source` (`upload` | `gate`, default `upload`). The anonymous endpoints exclude `source='gate'`. Gate frames are served only by `GET /api/v1/gate/events/<id>/image/<original|processed>/`, which requires an operator login. This replaces the earlier idea of showing gate frames in the existing `/images` UI.

**Bounded recognition time.** `QwenVLClient` builds its OpenAI client with no timeout, so it inherits the SDK default of 600 s with 2 retries. The decision cannot call the pipeline inline. Frames run in a module-level `ThreadPoolExecutor` (`GATE_WORKER_THREADS`, default 3, per Django worker process). The request waits with `concurrent.futures.wait(FIRST_COMPLETED)` until one of three things happens: every frame finishes, one plate reaches `GATE_CONSENSUS_MIN` agreeing frames (early exit), or `GATE_DECIDE_TIMEOUT` expires. Frames not yet started are cancelled and deleted. Frames still running get a done-callback that deletes them when they finish. When the deadline expires, the frames already read are still evaluated. The decision is `inference_timeout` only if the unfinished frames could have changed the outcome.

**Which plate a frame contributes.** The nearest vehicle's plate is taken to be the detection with the largest plate box that has OCR text. It counts once per frame. A car queued behind the one at the barrier cannot outvote it, and the ROI crop usually removes it anyway.

**Retry job.** `retry_stuck_images` re-processes any image stuck in `processing` for longer than `PROCESSING_TIMEOUT_MINUTES`. It uses the global `MAX_RETRIES`, not the per-image field. It now excludes `source='gate'`, so a frame abandoned at the deadline is never re-run behind the decision's back. The retention job removes gate frames that are older than one hour and not referenced by any event, which covers a worker process dying mid-decision.

**Retention**: plate images and the events that reference them are personal data. `purge_access_events` runs on the existing APScheduler (alongside `retry_stuck_images`) and deletes events and their images older than `GATE_EVENT_RETENTION_DAYS` (default 90). The numeric decision counters in Prometheus are unaffected.

### 6. ESP32: momentary pulses, hard interlock, fail-safe by construction

The relay board's normally-open contacts bridge `COM` to `UP`, `DOWN` and `STOP` respectively. Every command is a **momentary pulse** of `PULSE_MS` (default 400 ms), never a held closure — this is exactly what the guard's push-buttons do, so the controller sees nothing it does not already expect.

Firmware invariants:

- **De-energised is safe.** All relays idle open. Loss of power, WiFi, or the agent produces no contact closure, so the barrier holds its current position and the manual buttons still work.
- **UP/DOWN interlock.** The firmware refuses to energise `DOWN` within `INTERLOCK_MS` (default 1000 ms) of `UP` and vice versa, in code, regardless of what it is told.
- **Rate limit.** At most one motion command per `MIN_COMMAND_INTERVAL_MS` (default 3000 ms); excess commands are rejected with `429`.
- **Hardware watchdog** enabled; a hung firmware reboots into the idle (all-open) state.
- **Replay protection.** Each command carries a monotonic `nonce` and a `ts`; the device rejects a nonce it has seen and any `ts` outside a ±30 s window. Auth is a bearer token over the gate VLAN — this is a LAN-only, token-gated device, never internet-exposed.
- **Heartbeat.** Every 10 s the device POSTs `/api/v1/gate/heartbeat/` with its id, firmware version, uptime and last command result. Django updates `GateDevice.last_seen`; a device unseen for 30 s flips the `lpr_gate_controller_up` gauge to 0 and fires a Grafana alert.

Wiring notes for the install: use an opto-isolated relay module, power the relay coils from a separate 5 V supply (not the ESP32 3V3 rail) with grounds commoned, and keep the barrier's own mains wiring untouched — we only bridge the existing low-voltage command terminals. The guard's push-buttons remain wired in parallel and are never disconnected.

### 6a. The site controller: `BR6_CT` (APS)

Read from the site photos. The manufacturer's manual has not been obtained, so every behaviour below marked *verify* must be measured before wiring (tasks 0.4a–0.4f).

**Terminal map (low-voltage row)**

| Group | Terminals | Meaning for this project |
|---|---|---|
| Command inputs | `COM`, `UP`, `DOWN`, `STOP` | Where the ESP32 relays connect. **Already occupied** by a 4-pair UTP cable, one twisted pair per terminal, which almost certainly runs to the guard's existing button box. |
| `LOOP` | 2 | Connection for a buried loop coil. This suggests a built-in vehicle detector (*verify*). **Empty.** |
| `PHOTO SIGNAL INPUT` + `PHOTO POWER OUTPUT` | 2 + 2 | Photocell (IR safety beam) input, with the board supplying the photocell's power. **Empty.** |
| `VEHICLES DETECTOR SIGNAL` | 2 | Dry-contact input from an external loop detector. **Empty.** |
| `UP LIMIT OUTPUT`, `DOWN LIMIT OUTPUT` | 3 each | Arm-position outputs. Three terminals usually means a relay changeover (C/NO/NC) (*verify*). **Empty.** They can give the ESP32 real arm-position feedback (below). |
| `SPECIAL FOR MOTORCADE` | 2 | Convoy mode: holds the arm up while active (*verify*). Not used in stage 1. |
| `A`, `GND`, `B` (`485`) | 3 | RS-485 bus. The protocol is undocumented, so this project does **not** depend on it. Relay contacts work with any controller and keep the ESP32 electrically isolated. |
| `G`, `COM`, `R` (traffic light) | 3 | Red/green lane-light outputs. Optional for later. |
| `L`, `N`, `E` | 3 | **230 VAC mains. Never touched by this project.** |

The board also carries a 3-way **`TIMING` DIP switch**, most likely the auto-close delay (*verify*), a `COMMUNICATION MODULE` header, probably for the RF remote receiver, a 5 A fuse, and JQC-3FF motor relays with a motor-run capacitor. It is an AC-motor barrier.

**Connecting the ESP32 relays: in parallel with the existing cable, at the controller.** Do not remove the UTP cable to the guard's buttons. Each relay's contacts land on the same terminal as the matching cable pair, through a small Wago/terminal block if the spring terminal cannot take a third conductor. The guard's buttons keep working exactly as they do today, which is the design's baseline fallback (§1).

**Two checks that change the wiring (tasks 0.4a–0.4b).**

- *Input polarity.* On most boards of this kind the command inputs are pulled up to the board's logic supply (typically 5–12 VDC), and closing a terminal to `COM` triggers the action. Measure DC volts from `UP`, `DOWN` and `STOP` to `COM` at idle. If a terminal reads a voltage, it is normally-open: wire the relay's **NO** contact across that terminal and `COM`. The ESP32's ground is never connected to `COM`; the relay contacts provide the isolation.
- *`STOP` may be normally-closed.* On many barrier boards `STOP` is a normally-closed safety loop: the board runs only while `STOP` is linked to `COM`, and **opening** the link stops the arm. If `STOP` reads ~0 V to `COM` at idle while the arm works normally, the existing button box is holding it closed. In that case the ESP32's STOP relay goes **in series** in the STOP line through its **NC** contact, not in parallel through NO. The firmware logic is unchanged: a pulse still means "stop". A wrong guess here either disables the stop function or stops the barrier permanently, so this is measured, never assumed.

**Arm-position feedback (recommended; a small addition to §6).** The `UP LIMIT OUTPUT` and `DOWN LIMIT OUTPUT` changeover contacts can be read by two ESP32 GPIOs with internal pull-ups, one dry contact to ESP32 ground each, with no shared ground with the controller. The controller then *reports* where the arm is (`up`, `down` or `moving`). Two things follow:

- an `open` command is confirmed only when the arm actually reaches the up limit, not merely when the relay pulsed;
- a barrier left up (a stuck arm, or auto-close disabled) shows on the status page and in Grafana.

This needs one `arm_state` field in the heartbeat and on `GateDevice`. It is specified as a requirement in the controller spec, conditional on the limit outputs being wired.

### 7. Closing the barrier

Most barrier controllers have a built-in auto-close timer. **Prefer it.** Where it exists, the `DOWN` relay is wired but left unused by the agent, and `GATE_AUTO_CLOSE=controller`.

**At this site** the `BR6_CT` has what the closing strategy needs: a `TIMING` DIP (auto-close, *verify*), a `LOOP` input, a `PHOTO` input and a `VEHICLES DETECTOR SIGNAL` input. In the photos, however, **none of the safety inputs is wired**. Today the arm can only be protected by the guard's eyes. Whether the controller's own auto-close timer is enabled right now must be established (task 0.4c). If it is on with no sensor, the site already has a crush risk that exists independently of this project.

Recommendation, in order of effort:

1. **Photocell** across the lane at the arm line, wired to `PHOTO POWER OUTPUT` and `PHOTO SIGNAL INPUT`. This needs no road cutting and is the cheapest fix.
2. **Loop coil** cut into the road under the arm, wired to `LOOP` (built-in detector, *verify*) or through an external detector to `VEHICLES DETECTOR SIGNAL`. It detects vehicles better than a beam, and many boards use it for "close after the car has passed".

Once either is installed and tested, set `has_safety_input=True` on the gate. The controller's own timer or "close after pass" logic then closes the arm, and `GATE_AUTO_CLOSE` stays `controller`. Until then, automatic opening can go live, but closing stays with the controller timer or the guard.

Software-driven close (`GATE_AUTO_CLOSE=software`, after `GATE_AUTO_CLOSE_SECONDS`) must only be enabled when a loop detector or IR safety beam is wired into the controller's own safety input, so that the controller itself refuses to lower the arm onto a vehicle. Software must not be the only thing standing between a timer and a car roof. If no safety input exists on this controller, this stays off and the site keeps using the controller's timer or the guard's DOWN button.

The `STOP` relay is exposed in the operator UI as an emergency stop and is the one command with no rate limit.

### 8. Shadow mode is the default, and the rollout gate

`GATE_MODE=shadow` computes and logs every decision but the agent never sends a command. Stage 4 does not begin until a shadow run over real traffic shows: zero `granted` decisions for vehicles that should not have been granted, and a grant rate on registered vehicles that the site finds acceptable. The mode is read per-request from `GateDevice.is_enabled` and the setting, so it can be flipped without a rebuild.

### 9. Rate limiting and the 2 MB upload limit

`RATE_LIMIT_INCLUDE_PATHS` defaults to `/api/v1/ocr/` only, and the middleware treats any path not in that list as exempt — so `/api/v1/gate/*` is already outside the throttle with no change to the default config. The spec delta records this as an explicit, tested guarantee rather than an accident of the default, because a throttled gate is a stuck gate.

**Upload limit.** The limit today is inconsistent: `settings.py` defaults `UPLOAD_FILE_MAX_SIZE` to 1 MB, both `.env` examples set 1 MB, every compose file defaults to 10 MB, and `AGENTS.md` says 250 KB. This change sets one value, **2 MB (2097152 bytes)**, in all of those places. Every component — camera, agent, API, SPA — sits on the internal network, so bandwidth is not the constraint that the old small limit was guarding against, and 2 MB is enough for a full camera frame at normal JPEG quality.

- The limit is **per file**. `/api/v1/gate/decide/` accepts up to `GATE_BURST_FRAMES` files, so its request can be up to `GATE_BURST_FRAMES × UPLOAD_FILE_MAX_SIZE` (6 MB at defaults); the endpoint rejects more frames than configured before reading them.
- `FILE_UPLOAD_MAX_MEMORY_SIZE` is currently hard-coded to 250 KB, so any file over 250 KB is spooled to a temp file on disk. It is set equal to `UPLOAD_FILE_MAX_SIZE` so gate frames stay in memory — a 3-frame burst is ~6 MB of RAM per in-flight request, trivial on the gate host.
- `DATA_UPLOAD_MAX_MEMORY_SIZE` (250 KB) is left alone: Django excludes file parts from that check, so it does not limit images.
- The SPA reads `max_upload_bytes` from `/api/v1/config/` and needs no code change.

**Consequence for existing Docker deployments:** compose currently defaults to 10 MB, so on Docker this is a *reduction*. Manual uploads of phone photos between 2 and 10 MB through the SPA will start being rejected with `FILE_TOO_LARGE`. A site that still needs that sets `UPLOAD_FILE_MAX_SIZE` in its `.env`; the default is what changes, not the ability to override it.

`/api/v1/ocr/` stays rate-limited at `2/min` per IP, so the larger per-file limit does not materially widen the abuse surface on that endpoint.

### 10. Camera configuration lives in the database, managed from the admin UI

The first draft configured the camera through agent environment variables. That means editing a file on the gate host and restarting a container every time the camera is re-aimed, replaced, or re-addressed — and camera IPs on a site LAN do change. Instead, each camera is a `Camera` row edited from an admin-only page, `/admin/cameras`, in the SPA.

**Model.** `Camera` holds structured fields rather than one URL string, so the UI can validate each part and never has to parse credentials out of a URL:

- `name`, `is_enabled`
- `host` (IPv4 address or hostname), `rtsp_port` (default 554), `http_port` (default 80)
- `username`, `password_encrypted`
- `vendor`: `hikvision` | `dahua` | `generic`. Choosing a vendor pre-fills the three paths below; `generic` leaves them blank for manual entry.
  - Hikvision: main `/Streaming/Channels/101`, sub `/Streaming/Channels/102`, snapshot `/ISAPI/Streaming/channels/101/picture`
  - Dahua: main `/cam/realmonitor?channel=1&subtype=0`, sub `…&subtype=1`, snapshot `/cgi-bin/snapshot.cgi`
- `main_stream_path`, `sub_stream_path`, `snapshot_path`, `prefer_snapshot`
- `roi_x`, `roi_y`, `roi_w`, `roi_h` — normalised 0–1, so they survive a resolution change
- trigger tuning: `motion_threshold`, `settle_ms`, `cooldown_s`
- `config_version` (incremented on every save), `updated_by`, `updated_at`
- last test result: `last_test_at`, `last_test_ok`, `last_test_error`

`GateDevice` references a `Camera` by FK rather than embedding these fields, so a camera can be swapped for a gate by changing one dropdown.

Initial seed for this site: host `172.87.80.80`, username `admin`, entered by the gate admin through the UI on first setup — **not** committed in a fixture or migration.

**Credentials.** The password is write-only everywhere a human touches it:

- Encrypted at rest with Fernet, keyed by a new `GATE_CONFIG_ENCRYPTION_KEY` env var. A copied SQLite file or a backup does not yield the camera's admin password. Missing key → the camera pages refuse to save a password and say why.
- Never returned by any operator/admin API. Reads return `password_set: true|false`. The edit form shows an empty password box with "leave blank to keep the current password".
- Only one endpoint returns it decrypted: `GET /api/v1/gate/agent-config/`, authenticated by `GATE_AGENT_TOKEN`, which only the gate agent holds.
- Redacted (`admin:***@`) wherever a URL is logged, by both Django and the agent.

**Test connection.** `POST /api/v1/gate/cameras/test/` takes either a saved camera id or unsaved form values (so the admin can test *before* saving), and from the Django host:

1. opens a TCP connection to `host:rtsp_port` (reachability only; no video decoding in the `lpr-app` image, so no OpenCV/ffmpeg added to it);
2. fetches the snapshot URL over HTTP with **Digest** auth, falling back to Basic — Hikvision and Dahua require Digest by default, a detail that silently breaks naive clients;
3. returns the JPEG to the browser as a preview (not stored) plus a per-step pass/fail with a human-readable error (`connection refused`, `401 wrong password`, `path not found`, `timeout`).

The preview doubles as the canvas for the **ROI picker**: the admin drags a rectangle over the snapshot and it is saved as normalised coordinates. That is the practical way to aim the read zone; nobody should have to type pixel offsets.

**SSRF guard.** A "fetch this URL for me" endpoint is a server-side request forgery primitive: an admin (or a stolen admin session) could point it at other internal services. Mitigations, all required:

- `host` must resolve inside `GATE_CAMERA_ALLOWED_CIDRS` (default `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), checked on the *resolved* address after DNS, at both save time and test time;
- redirects are not followed; 5 s timeout; response capped at 5 MB and must be `image/jpeg`;
- `gate_admin` role only.

Note that the default allowlist **rejects `172.87.80.80`**, since it is not RFC 1918 space. The site has confirmed the address is internal. The gate host's `.env` therefore adds the camera's range explicitly. Add the narrowest range that covers the site's cameras: `172.87.80.80/32` for this single camera, or `172.87.80.0/24` if more will be added on that subnet:

```
GATE_CAMERA_ALLOWED_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,172.87.80.0/24
```

The shipped default stays RFC 1918 only. Every other deployment still has to opt in to a non-private range deliberately.

**Hot reload into the agent.** The agent polls `GET /api/v1/gate/agent-config/` every 30 s, sending the `config_version` it holds; the server replies `304` when unchanged. On a new version it closes the stream and reconnects with the new settings. The agent also reports its own view of the camera (`streaming`, `reconnecting`, `auth_failed`) in its status call, so the camera page shows both "Django can reach it" (test) and "the agent is actually streaming from it" (live status). If the config endpoint is unreachable, the agent keeps running on its last known config.

**Audit.** Every save writes a `GateConfigChange` row: who, when, which object, and which fields changed with old → new values — except the password, recorded only as `password: changed`. Camera changes can silently break the gate, so "who moved the ROI yesterday" must be answerable.

**Also in Django admin.** `Camera` and `GateDevice` are registered in `lpr_app/admin.py` with the same write-only password form field, as a fallback for when the SPA is down. Both paths write the same audit row.

### 11. Authentication for the admin pages

The SPA has no login today and every API endpoint is anonymous (only Django's own `/admin/` requires a user). That was acceptable for an OCR demo; it is not acceptable for pages that store camera credentials and can open a barrier.

- **Django session auth**, reusing `django.contrib.auth` and the superuser that `docker-entrypoint.sh` already creates. New endpoints: `POST /api/v1/auth/login/`, `POST /api/v1/auth/logout/`, `GET /api/v1/auth/me/` (returns user, roles and the CSRF token). The session cookie is `HttpOnly` and `SameSite=Lax`. Unsafe methods require the CSRF token in `X-CSRFToken`.
- **Same-origin and same-site cross-origin both work.** The browser calls the API directly at `BACKEND_API_URL`, which is `localhost:3000 → localhost:8000` in development. `SameSite` ignores ports, so the session cookie is still sent. The pieces that make this work:
  - `CORS_ALLOW_CREDENTIALS=True`, which only applies to the explicit `CORS_ALLOWED_ORIGINS` list;
  - `CSRF_TRUSTED_ORIGINS` defaulting to `CORS_ALLOWED_ORIGINS`;
  - the CSRF token returned in the `me`/`login`/`logout` response body, so the SPA never needs to read the cookie from another origin.

  A cross-*site* deployment (a different registrable domain) would need `SameSite=None; Secure` and is out of scope.
- **Login throttling.** After 5 failed attempts for one username from one IP, that pair is locked out for 5 minutes (`429 LOGIN_LOCKED`). Wrong usernames and wrong passwords get the same `401` message.
- **Roles** as Django groups created by a data migration: `gate_admin` (cameras, gate devices, everything) and `gate_operator` (vehicle registry, event log, manual override, emergency stop). Superusers have both.
- **Machine credentials** are separate from users: the agent uses `GATE_AGENT_TOKEN`; each ESP32 authenticates its heartbeat with its own `GateDevice` token. Neither can call admin endpoints.
- Existing public endpoints (`/api/v1/ocr/`, image list/detail, health, metrics) are unchanged in this change — locking those down is a separate decision.

**Alternative considered**: token auth (DRF / JWT) for the SPA. Rejected — it adds a dependency and puts a long-lived token in browser storage, for a same-origin internal app where Django sessions already work.

### 12. Manual overrides reach the barrier through the agent, and expire

An operator's `open`/`close`/`stop` creates an `AccessEvent` with decision `manual`. Because Django never contacts a controller (§1), the agent polls `GET /api/v1/gate/agent-commands/` about once a second. Each command is claimed atomically (`command_result=dispatched`) so it runs exactly once, and `stop` is returned first. The agent reports the outcome to `POST /api/v1/gate/events/<id>/command-result/`.

- A command not claimed within `GATE_COMMAND_TTL_SECONDS` (default 15 s) is marked `expired` and never sent. An `open` that fires a minute late, after the guard has walked away, is worse than one that never fires.
- In shadow mode, manual commands are recorded as `not_sent_shadow_mode` and never dispatched. Shadow means nothing actuates, whoever asks.

### 13. A simulated barrier, to test everything before the ESP32 exists

Recognition quality, lane geometry, the agent and the command path can all be proven before any wire touches the `BR6_CT`. A gate's `controller_type` is `esp32` or `simulator`. A simulated gate is driven by `services/barrier_simulator.py`, served at `/api/v1/gate/sim/<gate_id>/<command>`. It is the **reference implementation of the controller contract** (§6):

- bearer token (the gate's controller token, generated automatically);
- strictly increasing nonce and a ±30 s timestamp window;
- UP/DOWN interlock (409);
- one motion command per 3 s (429), with `stop` always allowed;
- responses shaped like the firmware's.

The agent reaches it through the `controller_url` in its config, exactly as it will reach the ESP32. The same agent code runs in both cases, and the firmware has an executable spec to be checked against.

The arm is a timestamp-driven state machine: `down → moving_up → up → (auto-close) → moving_down → down`, plus `stopped`. Travel time and auto-close are set per gate, mirroring the `TIMING` DIP. The state is advanced when read, so no background process is needed. It is exposed as `arm_state` on the gate, in `/api/v1/gate/status/` and as `lpr_gate_arm_up`, the same fields the limit-switch feedback in §6a will fill for the real barrier.

**Dispatch rule.** Shadow mode now means "no **physical** actuation". `gate_service.can_actuate(gate, mode)` is true for a live gate or a simulated gate. `GATE_MODE` can therefore stay `shadow` while the whole chain (camera → trigger → decision → command → arm) runs end to end against the simulator. A real ESP32 still moves only in `live` mode.

**Admin test page.** `/admin/lpr_app/gatedevice/<id>/test/` (gate_admin) runs uploaded photos through the real decision pipeline. It shows the decision, the plate, the evidence frame and the animated simulated arm, and has Open/Close/Stop buttons. These runs are `AccessEvent.is_test=True`: they appear in the log with a filter, and they are kept out of the gate metrics. A test never actuates a real controller (`command_result=not_sent_test`).

The simulator lives in the production service by design, because the site needs it on the deployed admin backend before the hardware arrives. It is inert unless a gate is explicitly set to `simulator`.

## Risks / Trade-offs

- **False grant (unauthorised vehicle enters).** Mitigated by exact-match-only, consensus across frames, and a confidence floor. Residual risk: two registered-looking plates that normalise identically — prevented by a uniqueness constraint on the normalised column.
- **False deny (registered vehicle waits).** The expected failure mode, and an acceptable one — the guard opens manually. Watched via `lpr_gate_decisions_total{decision="denied",reason="..."}`; a rising `no_consensus` or `no_plate` rate means the camera needs re-aiming or the lighting needs work (night-time IR, headlight glare and plate retroreflection are the usual culprits).
- **VLM latency spike or inference backend down.** The decide endpoint denies on timeout rather than hanging; `GATE_DECIDE_TIMEOUT` (default 8 s) bounds the agent wait. The gate reverts to manual, which is today's normal operation.
- **Camera addressing and credentials.** The site confirmed that `172.87.80.80` is an internal address. The block is not RFC 1918 (private `172.x` space is only `172.16`–`172.31`); it is public space in use internally. This is common, and it carries two residual risks:
  - hosts on the LAN cannot reach whoever really owns `172.87.80.0/24` on the internet. That does not matter for a camera;
  - if routing or NAT ever changes so the segment is reachable from outside, the camera is exposed with it.

  The camera should still sit on an isolated VLAN with no inbound path. The current camera password is guessable and should be rotated regardless of addressing (task 0.2). Credentials are entered through the admin UI and stored encrypted (§10), never in the repo, a fixture or a compose file.
- **Controller wiring assumptions.** The `BR6_CT` manual has not been obtained. Input polarity, whether `STOP` is normally-closed, the `TIMING` DIP meaning and the limit-output contact type all come from reading the board and are verified by measurement before any wire is connected (§6a). A wrong `STOP` assumption can disable the stop function, so it is measured, never assumed.
- **No safety sensor installed.** The `LOOP`, `PHOTO` and vehicle-detector inputs are empty (§7). Automatic *opening* does not add crush risk, because the arm moves away from the vehicle. Any automatic *closing* does, whether by software or by the controller's timer. A photocell is the recommended precondition for unsupervised operation.
- **Admin session compromise.** A stolen `gate_admin` session can change the camera, and through the test endpoint probe hosts in the allowed CIDRs. Bounded by the CIDR allowlist, the role split (operators cannot reach camera config), the audit log, and the fact that the session cookie is `HttpOnly`.
- **Lost encryption key.** If `GATE_CONFIG_ENCRYPTION_KEY` is lost, stored camera passwords cannot be decrypted; the agent reports `auth_failed` and the admin re-enters the password. Nothing else is lost. The key is backed up with the rest of the gate host `.env`.
- **Upload limit reduction on Docker.** See §9 — manual uploads between 2 and 10 MB will be rejected after this change unless overridden.
- **Personal data.** Plate numbers and vehicle images identify people. Retention is bounded (90 days default), the registry UI is behind Django admin auth, and the event log is read-only via the API.
- **Physical liability.** Anything that moves a barrier arm can injure. Stages 2 and 3 bench-test against LEDs, not the barrier; the live stage requires site electrical sign-off and a supervised burn-in.

## Migration Plan

All models are new. The schema migration adds `Vehicle`, `Camera`, `GateDevice`, `AccessEvent` and `GateConfigChange` and touches nothing existing, so it is safe on a live database and reversible by `migrate lpr_app <previous>`. A second, data-only migration creates the `gate_admin` and `gate_operator` groups with their permissions; its reverse deletes them.

The upload-limit change needs no migration. Deployments that relied on the 10 MB compose default and still need it set `UPLOAD_FILE_MAX_SIZE=10485760` in `.env` before pulling.

Rollout is staged (see tasks.md): survey → shadow → bench → supervised live → unsupervised. Rollback at any stage is `GATE_MODE=shadow` (software) or pulling one connector on the relay board (hardware); in both cases the gate returns to manual operation with the guard buttons untouched.

## Open Questions

- Camera make/model and whether a still-snapshot endpoint exists (decides the vendor preset, and whether the Test connection preview and ROI picker work out of the box or need a `generic` snapshot path).
- ~~Is the camera's address block internal?~~ **Resolved:** `172.87.80.80` is internal; add `172.87.80.0/24` (or `/32`) to `GATE_CAMERA_ALLOWED_CIDRS` on the gate host.
- Does any existing workflow depend on uploading files over 2 MB through the SPA on a Docker deployment?
- ~~Barrier controller make/model?~~ **Identified** as `BR6_CT VER:001` (APS). It has a `TIMING` DIP, `LOOP`, `PHOTO` and vehicle-detector inputs, and up/down limit outputs (§6a). **Still open:** the measured input polarity, whether `STOP` is NC, what the `TIMING` DIP positions mean and whether auto-close is enabled today, and where the existing UTP cable on `COM`/`UP`/`DOWN`/`STOP` terminates. Ask APS (the supplier) for the board manual.
- Is the read point far enough from the arm that ~3 s of recognition is invisible to the driver, or does the camera need to move back?
- One gate or two lanes / separate exit? The models support both; stage 1 assumes one entry lane.
- Who administers the registry day to day, and does it need to sync from an existing HR or vehicle-pass system rather than being hand-entered?
