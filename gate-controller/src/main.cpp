// Gate controller: ESP32-S3 + CC1101 433 MHz. Operates the barrier as one more
// remote, on commands from the gate agent over the internal WiFi (controller
// contract v2). See README.md.
//
// Invariants (design.md §9): the radio transmits only for an accepted command
// or an installer action in setup mode, never longer than radio::MAX_TX_MS; it
// is idle at boot, after every burst and on any failure; a hung loop is reset
// by the task watchdog.
#include <Arduino.h>
#include <WebServer.h>
#include <esp_task_wdt.h>

#include "api.h"
#include "arm.h"
#include "config.h"
#include "console.h"
#include "device.h"
#include "net.h"
#include "ota.h"
#include "pins.h"
#include "portal.h"
#include "radio.h"
#include "remote.h"

// ---------------------------------------------------------------- status LED
//
// RGB board (S3): a colour per state. One-colour board (ESP32 DevKit's D2):
// the same states as blink patterns.
//   setup mode        blue            fast blink (5 Hz)
//   no WiFi           red             short flash every second
//   dry run / clock   amber           slow blink (1 Hz)
//   ready             green           steady on
//   transmitting      white           on

enum LedState { LED_OFF, LED_SETUP, LED_NO_WIFI, LED_ATTENTION, LED_READY, LED_TX };

void led_show(LedState state) {
#if STATUS_LED_RGB
  static const uint8_t colours[][3] = {
      {0, 0, 0}, {0, 0, 24}, {24, 0, 0}, {24, 16, 0}, {0, 12, 0}, {16, 16, 16},
  };
  // Written only on a change: each write is an RMT transmission
  static int shown = -1;
  if (shown == int(state)) return;
  shown = int(state);
  const uint8_t* c = colours[state];
  neopixelWrite(PIN_STATUS_LED, c[0], c[1], c[2]);
#else
  static bool ready = false;
  if (!ready) {
    pinMode(PIN_STATUS_LED, OUTPUT);
    ready = true;
  }
  uint32_t t = millis();
  bool on;
  switch (state) {
    case LED_SETUP: on = (t / 100) % 2 == 0; break;
    case LED_NO_WIFI: on = t % 1000 < 100; break;
    case LED_ATTENTION: on = t % 1000 < 500; break;
    case LED_READY:
    case LED_TX: on = true; break;
    default: on = false;
  }
  digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
#endif
}

namespace device {

Config cfg;
gatecore::Guard guard;

namespace {
SemaphoreHandle_t mutex = nullptr;
String result;
}  // namespace

void lock() { xSemaphoreTake(mutex, portMAX_DELAY); }
void unlock() { xSemaphoreGive(mutex); }

void set_last_result(const String& r) {
  lock();
  result = r;
  unlock();
}

String last_result() {
  lock();
  String r = result;
  unlock();
  return r;
}

bool transmit_button(Button b) {
  const ButtonCode& code = cfg.buttons[b];
  if (!code.set || !radio::ready()) return false;
  led_show(LED_TX);
  bool ok = radio::transmit(gatecore::encode_burst(code.code, code.profile, cfg.repeats));
  led_show(LED_OFF);
  return ok;
}

uint32_t uptime_s() { return millis() / 1000; }

}  // namespace device

namespace {

const uint32_t WATCHDOG_S = 10;
WebServer server(80);
bool setup_mode = false;

bool setup_button_held() {
  pinMode(PIN_SETUP, INPUT_PULLUP);
  uint32_t start = millis();
  while (digitalRead(PIN_SETUP) == LOW) {
    if (millis() - start >= SETUP_HOLD_MS) return true;
    delay(20);
  }
  return false;
}

void status_light() {
  LedState state = setup_mode ? LED_SETUP
                   : !net::connected() ? LED_NO_WIFI
                   : (device::cfg.dry_run || !net::clock_synced()) ? LED_ATTENTION
                   : LED_READY;
  led_show(state);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  device::mutex = xSemaphoreCreateMutex();
  esp_task_wdt_init(WATCHDOG_S, true);
  esp_task_wdt_add(nullptr);

  config_store::load(device::cfg);
  ota::begin();
  device::guard.restore_nonce(config_store::load_nonce());
  // The radio comes up idle before anything else can happen
  radio::begin(device::cfg.frequency_mhz, device::cfg.power_dbm);
  arm::begin();

  Serial.printf("Gate controller %s, gate %u, dry run %s\n", GATE_FW_VERSION, unsigned(device::cfg.gate_id),
                device::cfg.dry_run ? "on" : "off");
  setup_mode = !device::cfg.provisioned() || setup_button_held();
  if (setup_mode) {
    portal::begin(server);
    return;
  }

  net::begin(device::cfg);
  portal::begin_status(server);
  api::begin(server);
  net::start_heartbeat(device::cfg, [](JsonDocument& doc) {
    doc["firmware_version"] = GATE_FW_VERSION;
    doc["board"] = ota::board();
    doc["uptime_s"] = device::uptime_s();
    doc["rf_tx_count"] = radio::tx_count();
    doc["dry_run"] = device::cfg.dry_run;
    doc["arm_state"] = arm::state();
    String last = device::last_result();
    if (last.length()) doc["last_command_result"] = last;
    remote::describe(doc);
    ota::describe(doc);
  });
}

void loop() {
  esp_task_wdt_reset();
  console::tick();
  server.handleClient();
  status_light();
  portal::tick();
  if (!setup_mode) {
    net::tick();
    ota::tick();
    remote::poll();
    const char* confirmed = arm::poll();
    if (confirmed) device::set_last_result(confirmed);
  }
  delay(2);
}
