# gate-agent

Watches a gate camera, asks the LPR service for a decision when a vehicle stops at the barrier, and relays barrier commands to the gate controller. The controller is either the ESP32 relay board or the **simulated barrier** served by the LPR service. Design: `openspec/changes/2026-09-22-anpr-gate-automation/design.md`.

```
camera ──frames──▶ gate-agent ──burst──▶ LPR service /api/v1/gate/decide/
                       │                         │ decision (+ actuate?)
                       └──── open/close/stop ────┴─▶ controller (ESP32, or /api/v1/gate/sim/<id>/)
```

The agent holds no camera or controller settings of its own. It loads them from the LPR service (`/api/v1/gate/agent-config/`) and picks up changes made in the admin within 30 s, without restarting.

## Modes

```bash
python -m gate_agent run                                    # the service
python -m gate_agent decide --gate 1 a.jpg b.jpg c.jpg      # photos as one burst, print the decision
python -m gate_agent replay --gate 1 ./recorded-frames      # recorded frames through the trigger
```

`decide` and `replay` only ever open a **simulated** barrier. To drive a real controller from them, pass `--allow-real-controller`.

## Environment

| Variable | Default | |
|---|---|---|
| `LPR_API_URL` | `http://lpr-app:8000` | LPR service base URL |
| `GATE_AGENT_TOKEN` | — | Must equal `GATE_AGENT_TOKEN` on the LPR service |
| `AGENT_METRICS_PORT` | `9101` | Prometheus metrics (`lpr_gate_agent_*`) |
| `AGENT_CONFIG_INTERVAL` | `30` | Seconds between config syncs |
| `AGENT_COMMAND_POLL_INTERVAL` | `1` | Seconds between manual-command polls |
| `AGENT_STATUS_INTERVAL` | `15` | Seconds between camera status reports |
| `AGENT_FRAME_INTERVAL` | `0.2` | Seconds between frames for the presence trigger (the loop paces itself, so this is the real rate) |
| `AGENT_PREBUFFER_FRAMES` | `3` | Frames kept from before the trigger, used as the recognition burst |
| `AGENT_MOVING_READ_SECONDS` | `2.0` | Read a vehicle that never stops after this long in the zone (`0` waits for a stop) |
| `AGENT_RTSP_TRANSPORT` | `tcp` | RTSP transport. Over UDP a lost packet stalls the stream |
| `AGENT_RTSP_TIMEOUT_SECONDS` | `5` | Socket timeout for RTSP; without it FFmpeg waits 30s before reporting a stalled stream |
| `AGENT_BURST_INTERVAL` | `0.4` | Seconds between frames in a recognition burst |
| `AGENT_PRESENCE_FACTOR` | `3.0` | Presence threshold = max(camera motion threshold × factor, 0.04) |
| `AGENT_MAX_ATTEMPTS` | `2` | Reads per vehicle when the first one is denied |
| `AGENT_MAX_OCCUPIED_SECONDS` | `300` | After this, something standing in the zone is accepted as background |
| `AGENT_REQUEST_TIMEOUT` | `10` | Seconds for API calls other than decide |
| `AGENT_JPEG_QUALITY` | `90` | Burst frame quality; lowered only if a frame exceeds the upload limit |

## When does it read a plate?

The trigger compares a small greyscale copy of the camera's region of interest against two references: the previous frame (**motion**) and a slowly learned picture of the empty lane (**presence**).

- A read starts when motion stops for `settle_ms` while something is present. A car that pulls up and stops triggers a read. A pedestrian walking through does not, because nothing is left present once they are gone.
- A denied vehicle is read again after `cooldown_s`, up to `AGENT_MAX_ATTEMPTS` times in total.
- A granted vehicle is not read again. The next read needs the lane to clear, or a different vehicle to replace it.

`motion_threshold`, `settle_ms`, `cooldown_s` and the ROI are set per camera in the admin.

## Missing the plate of an arriving vehicle

Three things decide whether the plate is caught, and all three are tunable.

**1. How fast frames arrive.** The loop paces itself to `AGENT_FRAME_INTERVAL`,
so 0.2 really means five frames a second. Watch `lpr_gate_agent_fps`: if it sits
well below `1 / AGENT_FRAME_INTERVAL`, the camera is the limit — check
`lpr_gate_agent_grab_seconds`.

**2. Where the frames come from.** A snapshot costs the camera one HTTP request
per frame, and many cameras answer HTTP 500 above about two a second. RTSP costs
one connection whatever the rate, so for a fast lane turn off *Prefer snapshots
over RTSP* on the camera. The agent reads RTSP in its own thread and always uses
the newest frame, never a buffered one.

**3. When the read fires.** A vehicle that stops is read once it has been still
for `settle_ms`. A vehicle that rolls through never stops, so it is read anyway
after `AGENT_MOVING_READ_SECONDS` in the zone. The burst itself uses the frames
kept from *before* the trigger (`AGENT_PREBUFFER_FRAMES`), which show the vehicle
arriving, rather than frames captured afterwards when it may already have moved on.

If plates are still missed, in order: shrink the read zone to where the plate
actually is, lower `settle_ms` on the camera, then lower `AGENT_FRAME_INTERVAL`.

## A vehicle arrived and no event appeared

That is a different fault: the vehicle was never noticed, so nothing was even
sent for recognition. Open **Monitor**; under each live view the agent reports
what its trigger sees:

```
Vehicle in the zone · 5.0 frames/s
Motion    ▓▓▓░░░░  0.004 / 0.020
Presence  ▓▓▓▓▓▓▓  0.184 / 0.060
```

- **Presence stays below its threshold while a vehicle is there** — the vehicle
  fills too little of the read zone. Shrink the zone to the part of the lane the
  vehicle occupies, or lower `motion_threshold` on the camera (presence is
  `motion_threshold × AGENT_PRESENCE_FACTOR`, at least 0.04).
- **Only some vehicles are read, and always the same kind** — the read zone
  covers more than one lane. Presence is measured over the whole zone, so
  anything standing in it (a vehicle leaving, a parked motorbike, someone
  waiting) keeps it occupied, and the next vehicle to arrive is taken for the
  one already there rather than read. A zone should cover where one vehicle
  stops, nothing more. Draw it on the live view on the camera page.
- **It says "Lane empty" with a vehicle in front of the camera** — the agent is
  reading a different camera or a stale frame. Check the live view actually moves.
- **Nothing is reported at all** — the agent is not running, or not watching this
  camera: `docker compose logs gate-agent`.
- **The state sticks at "Vehicle in the zone"** — the lane never cleared, so the
  next vehicle is treated as the same one. A camera aimed at a parked car does
  this; the agent accepts it as background after `AGENT_MAX_OCCUPIED_SECONDS`.

## Testing recognition before the ESP32 exists

Everything below uses the **simulated barrier**. It speaks exactly the protocol the ESP32 will (token, nonce, timestamp window, UP/DOWN interlock, rate limit), so nothing changes later except the gate's controller type and URL.

1. On the LPR service, generate the secrets and put them in `.env`:
   ```bash
   python manage.py generate_gate_secrets    # GATE_CONFIG_ENCRYPTION_KEY, GATE_AGENT_TOKEN
   ```
   Add the camera's network if it is not RFC 1918, e.g. `GATE_CAMERA_ALLOWED_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,172.87.80.0/24`.
2. In Django admin (`/admin/`):
   - **Vehicles**: add the authorised plates.
   - **Gate devices**: add a gate with *Controller type = Simulated barrier*. The token is generated for you. Set travel and auto-close times in the "Simulated barrier settings" section.
3. **Test without a camera, from the admin.** Open *Gate devices → Test recognition* and upload two or three photos of the same vehicle. The page shows:
   - the decision, reason, plate read, confidence and matched vehicle;
   - the evidence frame with the detected plate;
   - the barrier arm opening and, after the auto-close time, closing again.

   The Open, Close and Stop buttons drive the simulated arm directly. Test runs are flagged `is_test` and kept out of the gate metrics.
4. **Test the agent path with photos** (same pipeline, over HTTP):
   ```bash
   cd gate-agent
   LPR_API_URL=http://localhost:8000 GATE_AGENT_TOKEN=... python -m gate_agent decide --gate 1 car1.jpg car2.jpg
   ```
5. **Test with the real camera.** In the web UI, open *Cameras* → *Add camera* (vendor, host, user, password), press **Test connection** until every step passes, drag the read zone on the snapshot, and save. Then assign the camera to the gate in *Gates*. (The Django admin has the same fields and a *Test connection* action, without the preview.) Then run the agent:
   ```bash
   docker compose --profile core --profile gate up -d
   ```
   The camera's agent status on its page turns *Streaming*. Drive a car up to the barrier and watch the arm on the *Gate* page; decisions appear in *Events*.

`GATE_MODE` can stay `shadow` throughout. Shadow mode forbids **physical** actuation, and a simulated barrier moves no hardware. When the ESP32 is installed, switch the gate's controller type to *ESP32*, set its URL and token, and a real barrier only moves once `GATE_MODE=live`.

## Tests

```bash
cd gate-agent
python -m unittest discover -s tests -t .
```

The LPR service also runs this agent end to end against a live test server and the simulated barrier (`lpr_app/tests/test_gate_agent_e2e.py`).
