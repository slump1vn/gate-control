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

- One of the two supported boards: ESP32-S3-DevKitC-1 (N8R2 or N16R8), or an
  ESP-WROOM-32 DevKit (the common 30-pin board, USB-C or micro-USB, CH340C or CP2102)
- CC1101 module **labelled 433 MHz**, with an SMA connector (for example Ebyte
  E07-M1101D-SMA). A module tuned for 315 or 868 MHz transmits poorly at 433.
- 433 MHz SMA antenna, or a 17.3 cm straight wire
- 5 V ≥ 1 A USB power supply on its own socket
- IP65 **plastic** enclosure (metal blocks the radio), cable glands
- For bench tests: a generic 433 MHz learning receiver relay board of the
  same code family, plus an LED

## Wiring

| CC1101 | ESP32-S3-DevKitC-1 | ESP-WROOM-32 DevKit (label on the board) | |
|---|---|---|---|
| VCC | 3V3 | 3V3 | **never 5 V / VIN** |
| GND | GND | GND | |
| SCK | GPIO12 | D18 | |
| MOSI (SI) | GPIO11 | D23 | |
| MISO (SO) | GPIO13 | D19 | |
| CSN | GPIO10 | D5 | |
| GDO0 | GPIO4 | D4 | transmit data |
| GDO2 | GPIO5 | D16 | receive data (capture only) |
| arm UP limit (optional) | GPIO6 | D32 | dry contact to GND |
| arm DOWN limit (optional) | GPIO7 | D33 | dry contact to GND |
| status LED | on-board RGB | on-board "D2" LED | |

On the 30-pin ESP-WROOM-32 DevKit every CC1101 wire lands on the header row
with 3V3/GND/D15/D2/D4…D23. Leave D12, D2 and D15 alone (they decide how the
chip boots) and D34/D35/VP/VN (inputs only, no pull-ups).

Status LED: setup mode = blue / fast blink; no WiFi = red / a short flash
every second; dry run or clock not synced = amber / slow blink; ready =
green / steady.

Optional arm feedback: if the controller's `UP LIMIT OUTPUT` / `DOWN LIMIT
OUTPUT` are **dry contacts** (check with a meter: no voltage across them),
wire each contact between the pins above and ESP32 GND, and build with
`-DGATE_ARM_FEEDBACK=1`. An `open` is then confirmed in the heartbeat
(`open: ok` or `open: not_confirmed`). Without it, commands are reported as
`sent (unconfirmed)`. Never connect a powered output to a GPIO.

Pins are in `include/pins.h`.

## Build, test, flash

```bash
pip install platformio
cd gate-controller
pio test -e native            # host tests: signature, nonce, interlock, rate limit, RF frames
pio run -e esp32s3 -t upload  # ESP32-S3: flash over the USB port marked "USB"
pio run -e esp32dev -t upload # ESP-WROOM-32 DevKit
pio device monitor            # logs, and the setup-mode password on first boot
```

CI (`.github/workflows/gate-controller-build.yml`) runs the tests and publishes
the artifact `gate-controller-firmware`, with one image per board to flash at
**0x0**: `gate-controller-esp32s3-merged.bin` and
`gate-controller-esp32dev-merged.bin` (bootloader, partition table and
firmware in one file).

Without PlatformIO, flash the merged image from a browser (Chrome or Edge):
open https://espressif.github.io/esptool-js/, **Connect**, pick the board's
serial port (CH340/CP210x: install its USB driver first if none appears),
set the flash address to `0x0`, choose the `-merged.bin` file for the board
and **Program**. Or from a terminal: `pip install esptool`, then
`esptool.py --chip esp32 write_flash 0x0 gate-controller-esp32dev-merged.bin`
(`--chip esp32s3` for the S3). If the board does not start flashing, hold
BOOT while it connects. Open a serial monitor at 115200 baud afterwards: the
first boot prints the setup-mode WiFi name and password.

## Setup mode

The device starts in setup mode on first boot, or when the BOOT button is held
for 5 seconds at power-on. The LED is blue. It opens a WiFi network
`gate-ctl-xxxx`. Its password is generated on first boot and printed on the USB
console: **write it on the device label**. Join it and open `http://192.168.4.1/`.

1. **Network and gate.** WiFi SSID and password, the gate id and controller
   token from `/manage/gates` (controller type *ESP32 + 433 MHz remote*), and
   the heartbeat URL, e.g. `http://192.168.2.80:8002/api/v1/gate/heartbeat/` (through the
   `live-gateway` nginx, port `LIVE_GATEWAY_PORT`, so a device that drops mid-request cannot hold a server thread).
2. **Buttons**, one of two ways:
   - **pair** (preferred): put the barrier receiver in LEARN mode (see its
     manual), then press *pair* for that button. The device sends its own code,
     generated once, so it can be told apart from the guards' remotes.
   - **capture**: press *capture* and hold the guard remote's button near the
     device for the 10 seconds. The code is stored only if the same frame was
     received at least twice.
3. **test** each button. The barrier moves.
4. **Dry run** is on after provisioning: every command is checked and
   answered, but nothing is transmitted. Leave it on until the bench tests pass.
5. **Save and restart.** Setup mode also ends by itself after 10 minutes
   without use. It never serves the command API.

## The device's own page, and the serial console

In normal mode the same page is served at `http://<device IP>/ui/`, behind
HTTP Basic auth: user `admin`, password the setup password on the device
label (the page can open the gate). It shows:

- **Connection checks**: the CC1101 (answers on SPI, PARTNUM/VERSION of a
  real CC1101, frequency, power), WiFi (primary or backup, SSID, IP, signal),
  the last heartbeat (HTTP status and age), clock and dry run.
- **Listen to a remote** for 10 seconds: edges and frames heard, the strongest
  signal in dBm, and whether it is a copyable fixed code, probably a rolling
  code, or not an EV1527/PT2262 signal at all. Reads only; stores nothing.
- **Try the gate**: Open / Close / Stop send the stored codes now, even in dry
  run, still under the UP/DOWN interlock and the 3-second rate limit.
- Pair / copy each button (fingerprints only), and the network form, including
  a **backup WiFi**: when the primary cannot be joined for 20 seconds the
  device switches to the backup, and keeps alternating until one lets it in.

Over the USB serial port (115200 baud), one JSON object per line:
`{"cmd":"show"}`, `{"cmd":"set","ssid":"…","wifipw":"…","ssid2":"…","wifipw2":"…","gate":1,"secret":"…","hburl":"…"}`,
`{"cmd":"restart"}`. Secrets are never printed. The device also prints the
WiFi network it joined and its IP address.

A **static address** on the primary WiFi:
`{"cmd":"set","ip":"172.87.80.254","mask":"255.255.252.0","gw":"172.87.80.1","dns":"192.168.2.19"}`
(DNS defaults to the gateway), `{"cmd":"set","ip":"dhcp"}` to go back to DHCP;
also on the device page. The backup WiFi always uses DHCP, so a wrong static
address still leaves a way in. Update the gate's controller address in
`/manage/gates` to match.

Flashing through a network serial bridge: use Espressif's `esp_rfc2217_server`
(`pip install esptool`, then `esp_rfc2217_server -v -p 2217 COM3` on the PC the
board is plugged into). It plays the reset sequence on that PC, so esptool's
auto-reset works and nobody has to hold BOOT. pyserial's example server only
forwards DTR/RTS over the network, too late for the reset timing. Either one
resets the board each time a client connects.

## Updating over WiFi

Once a board runs firmware with OTA (this version onwards), it is updated
from `/manage/gates` → *Firmware*: upload `gate-controller-<board>-app.bin`
from the CI artifact (not the `-merged.bin`), then **Install**. The agent sends
the controller a signed `POST /update` with a one-time download path, the
image's SHA-256, size and version; the controller downloads the image from the
server its heartbeat goes to (through `live-gateway`), checks the SHA-256,
writes it to the other app slot and restarts into it, about 30 seconds without
radio. The new firmware is on probation: it keeps itself only once a heartbeat
is accepted, within 5 minutes; otherwise, or if it crashes first, the board
goes back to the previous firmware by itself. Progress and the outcome come
back in the heartbeat (`update`). Settings, WiFi and remote codes are kept.

The image carries `GATEFW:<version>:<board>:END`, read by the server on
upload, so a build for another board is never offered. Both app slots are
1.25 MB (default partition table); a firmware outgrowing that needs a new
partition table, flashed over USB once.

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
  UP/DOWN interlock (the opposite command within 1 s), `429` the same motion command again within 3 s (STOP is exempt),
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
