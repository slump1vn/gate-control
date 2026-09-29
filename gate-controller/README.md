# Gate controller: ESP32-S3 + CC1101 (433 MHz)

Firmware for the device that moves the barrier. It behaves as **one more
remote control** for the barrier's 433 MHz receiver, and takes its orders from
the gate agent over the internal WiFi. Nothing is wired into the barrier
controller's command terminals. Unplugging the device returns the gate to
exactly how it was.

Plan and rationale: `openspec/changes/2026-09-28-esp32-rf-gate-controller/`.

## Before buying anything: will it work at this gate?

It works only if the barrier's remotes use a **fixed or learning code** (encoder
chip EV1527, HS1527, RT1527 or PT2262), and only if **UP, DOWN and STOP are
separate buttons** with separate codes. Open a guard's remote and read the chip:

| Chip | Result |
|---|---|
| EV1527 / HS1527 / RT1527 / PT2262 | Works |
| HCS301 / HCS200 (KeeLoq, rolling code) | Does not work: use the relay design instead |
| One button that cycles open → stop → close | Does not work: use the relay design instead |

## Parts (per gate)

- ESP32-S3-DevKitC-1 (N8R2 or N16R8)
- CC1101 module **labelled 433 MHz**, with an SMA connector (for example Ebyte
  E07-M1101D-SMA). A module tuned for 315 or 868 MHz transmits poorly at 433.
- 433 MHz SMA antenna, or a 17.3 cm straight wire
- 5 V ≥ 1 A USB power supply on its own socket
- IP65 **plastic** enclosure (metal blocks the radio), cable glands
- For bench tests: a generic 433 MHz learning receiver relay board of the
  same code family, plus an LED

## Wiring

| CC1101 | ESP32-S3 | |
|---|---|---|
| VCC | 3V3 | **never 5 V** |
| GND | GND | |
| SCK | GPIO12 | |
| MOSI (SI) | GPIO11 | |
| MISO (SO) | GPIO13 | |
| CSN | GPIO10 | |
| GDO0 | GPIO4 | transmit data |
| GDO2 | GPIO5 | receive data (capture only) |

Optional arm feedback: if the controller's `UP LIMIT OUTPUT` / `DOWN LIMIT
OUTPUT` are **dry contacts** (check with a meter: no voltage across them),
wire each contact between GPIO6 / GPIO7 and ESP32 GND, and build with
`-DGATE_ARM_FEEDBACK=1`. An `open` is then confirmed in the heartbeat
(`open: ok` or `open: not_confirmed`). Without it, commands are reported as
`sent (unconfirmed)`. Never connect a powered output to a GPIO.

Pins are in `include/pins.h`.

## Build, test, flash

```bash
pip install platformio
cd gate-controller
pio test -e native            # host tests: signature, nonce, interlock, rate limit, RF frames
pio run -e esp32s3 -t upload  # flash over the USB port marked "USB"
pio device monitor            # logs, and the setup-mode password on first boot
```

CI (`.github/workflows/gate-controller-build.yml`) runs the tests and publishes
`gate-controller-esp32s3.bin` as a build artifact.

## Setup mode

The device starts in setup mode on first boot, or when the BOOT button is held
for 5 seconds at power-on. The LED is blue. It opens a WiFi network
`gate-ctl-xxxx`. Its password is generated on first boot and printed on the USB
console: **write it on the device label**. Join it and open `http://192.168.4.1/`.

1. **Network and gate.** WiFi SSID and password, the gate id and controller
   token from `/manage/gates` (controller type *ESP32 + 433 MHz remote*), and
   the heartbeat URL, e.g. `http://192.168.2.80:8000/api/v1/gate/heartbeat/`.
2. **Buttons**, one of two ways:
   - **pair** (preferred): put the barrier receiver in LEARN mode (see its
     manual), then press *pair* for that button. The device sends its own code,
     generated once, so it can be told apart from the guards' remotes.
   - **capture**: press *capture* and hold the guard remote's button near the
     device for the 6 seconds. The code is stored only if the same frame was
     received at least twice.
3. **test** each button. The barrier moves.
4. **Dry run** is on after provisioning: every command is checked and
   answered, but nothing is transmitted. Leave it on until the bench tests pass.
5. **Save and restart.** Setup mode also ends by itself after 10 minutes
   without use. It never serves the command API.

## Capturing a remote from the admin UI

Buttons can also be captured without setup mode, once the device is on the
network: `/manage/gates` → *Remote codes*. Press **Capture** for a button,
then hold that button of the guard's remote near the device for the 6
seconds shown. When the code has been heard (twice, as in setup mode),
press **Save**. The device keeps serving commands while it listens: an
open, close or stop in the meantime cancels the capture. The code never
leaves the device. The page shows fingerprints only, and the gate's change
history records which fingerprint replaced which.

## Normal mode

- LED: green = ready; amber = dry run or clock not synced yet; red = no WiFi.
- `POST /capture {button, seconds, job}` (answers 202, result in the next
  heartbeat) and `POST /capture/save {button}`, from the admin UI through
  the agent.
- `POST /open`, `/close`, `/stop` and `GET /status`, controller contract v2:
  signed with `X-Gate-Nonce`, `X-Gate-Ts` and `X-Gate-Sig`. The token itself is
  never sent. The gate agent does this. There is nothing to call by hand.
- Refusals: `401` bad signature or stale timestamp, `409` replayed nonce or
  UP/DOWN interlock, `429` another motion command within 3 s (STOP is exempt),
  `501` no code stored for that button, `503` clock not synced.
- Results: `sent` (radio is one-way: unconfirmed), `dry_run`.
- Heartbeat every 10 s to the LPR service: firmware, uptime, WiFi signal,
  clock, dry run, transmissions, arm state, last command result. A device that
  boots without NTP gets the time from the heartbeat reply.
- WiFi loss: reconnect with backoff, restart the WiFi stack after 5 minutes,
  reboot after 15. Nothing is transmitted meanwhile.

## Radio limits

433.05–434.79 MHz, at most +10 dBm (enforced in firmware), bursts under 1.5 s
(about 0.45 s per command), at most one motion command per 3 s. Confirm the
current Vietnamese short-range device regulation before installing (task 0.5).

## Rollback

Unplug the device. The barrier, the guards' remotes and the buttons are
unaffected. In software, set `GATE_MODE=shadow` or turn dry run back on.
