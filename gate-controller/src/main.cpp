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
#include "device.h"
#include "net.h"
#include "pins.h"
#include "portal.h"
#include "radio.h"

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
  neopixelWrite(PIN_STATUS_LED, 16, 16, 16);
  bool ok = radio::transmit(gatecore::encode_burst(code.code, code.profile, cfg.repeats));
  neopixelWrite(PIN_STATUS_LED, 0, 0, 0);
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
  static uint32_t next = 0;
  if (int32_t(millis() - next) < 0) return;
  next = millis() + 1000;
  if (setup_mode) {
    neopixelWrite(PIN_STATUS_LED, 0, 0, 24);  // blue
  } else if (!net::connected()) {
    neopixelWrite(PIN_STATUS_LED, 24, 0, 0);  // red
  } else if (device::cfg.dry_run || !net::clock_synced()) {
    neopixelWrite(PIN_STATUS_LED, 24, 16, 0);  // amber
  } else {
    neopixelWrite(PIN_STATUS_LED, 0, 12, 0);  // green
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  device::mutex = xSemaphoreCreateMutex();
  esp_task_wdt_init(WATCHDOG_S, true);
  esp_task_wdt_add(nullptr);

  config_store::load(device::cfg);
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
  api::begin(server);
  net::start_heartbeat(device::cfg, [](JsonDocument& doc) {
    doc["firmware_version"] = GATE_FW_VERSION;
    doc["uptime_s"] = device::uptime_s();
    doc["rf_tx_count"] = radio::tx_count();
    doc["dry_run"] = device::cfg.dry_run;
    doc["arm_state"] = arm::state();
    String last = device::last_result();
    if (last.length()) doc["last_command_result"] = last;
  });
}

void loop() {
  esp_task_wdt_reset();
  server.handleClient();
  status_light();
  if (setup_mode) {
    portal::tick();
  } else {
    net::tick();
    const char* confirmed = arm::poll();
    if (confirmed) device::set_last_result(confirmed);
  }
  delay(2);
}
