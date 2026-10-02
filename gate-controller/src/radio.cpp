#include "radio.h"

#include <ELECHOUSE_CC1101_SRC_DRV.h>
#include <driver/rmt.h>
#include <esp_task_wdt.h>
#include <soc/gpio_reg.h>
#include <soc/soc_caps.h>

#include "pins.h"

namespace radio {

namespace {

// The last transmit channel (3 on the S3, 7 on the ESP32): Arduino's
// neopixelWrite (the S3's status LED) takes the lowest free one for itself
const rmt_channel_t TX_CHANNEL = rmt_channel_t(SOC_RMT_TX_CANDIDATES_PER_GROUP - 1);
// RMT durations are 15-bit; longer levels are split across items
const uint32_t RMT_MAX_TICKS = 32767;
const size_t CAPTURE_MAX_EDGES = 2048;

bool ok = false;
uint32_t transmissions = 0;
float tuned_mhz = 0;
int8_t tuned_dbm = 0;
int capture_rssi = 0;
bool listening = false;
uint32_t listen_until = 0;

volatile uint32_t edge_at = 0;
volatile size_t edge_count = 0;
uint32_t edges[CAPTURE_MAX_EDGES];
volatile bool edge_levels[CAPTURE_MAX_EDGES];

// Back to idle: the CC1101 stops transmitting, and the RMT holds GDO0 LOW (its idle level)
void idle() { ELECHOUSE_cc1101.setSidle(); }

void IRAM_ATTR on_edge() {
  uint32_t now = micros();
  size_t n = edge_count;
  if (n < CAPTURE_MAX_EDGES) {
    edges[n] = now - edge_at;
    // The level that just ended is the opposite of the one now on the pin. Read
    // the register directly: digitalRead lives in flash, which an ISR must not
    // touch while NVS is being written.
    edge_levels[n] = !((REG_READ(GPIO_IN_REG) >> PIN_CC1101_GDO2) & 1u);
    edge_count = n + 1;
  }
  edge_at = now;
}

void append(std::vector<rmt_item32_t>& items, bool& half, bool level, uint32_t us) {
  while (us > 0) {
    uint32_t ticks = us > RMT_MAX_TICKS ? RMT_MAX_TICKS : us;
    us -= ticks;
    if (!half) {
      rmt_item32_t item = {};
      item.level0 = level;
      item.duration0 = ticks;
      items.push_back(item);
      half = true;
    } else {
      items.back().level1 = level;
      items.back().duration1 = ticks;
      half = false;
    }
  }
}

}  // namespace

bool begin(float frequency_mhz, int8_t power_dbm) {
  // GDO0 stays LOW until the RMT takes it over below
  pinMode(PIN_CC1101_GDO0, OUTPUT);
  digitalWrite(PIN_CC1101_GDO0, LOW);
  pinMode(PIN_CC1101_GDO2, INPUT);

  ELECHOUSE_cc1101.setSpiPin(PIN_CC1101_SCK, PIN_CC1101_MISO, PIN_CC1101_MOSI, PIN_CC1101_CSN);
  ELECHOUSE_cc1101.setGDO(PIN_CC1101_GDO0, PIN_CC1101_GDO2);
  if (!ELECHOUSE_cc1101.getCC1101()) {
    log_e("CC1101 not found on SPI: check wiring and 3.3 V supply");
    ok = false;
    return false;
  }
  ELECHOUSE_cc1101.Init();
  ELECHOUSE_cc1101.setCCMode(0);       // asynchronous serial: GDO0 is the data line
  ELECHOUSE_cc1101.setModulation(2);   // ASK/OOK
  ELECHOUSE_cc1101.setMHZ(frequency_mhz);
  tuned_mhz = frequency_mhz;
  tuned_dbm = power_dbm > 10 ? 10 : power_dbm;
  ELECHOUSE_cc1101.setPA(power_dbm > 10 ? 10 : power_dbm);
  idle();

  rmt_config_t cfg = RMT_DEFAULT_CONFIG_TX(gpio_num_t(PIN_CC1101_GDO0), TX_CHANNEL);
  cfg.clk_div = 80;  // 80 MHz / 80 = 1 tick per microsecond
  cfg.tx_config.idle_output_en = true;
  cfg.tx_config.idle_level = RMT_IDLE_LEVEL_LOW;
  if (rmt_config(&cfg) != ESP_OK || rmt_driver_install(TX_CHANNEL, 0, 0) != ESP_OK) {
    log_e("RMT setup failed");
    ok = false;
    return false;
  }
  ok = true;
  log_i("CC1101 ready at %.2f MHz, %d dBm", frequency_mhz, power_dbm > 10 ? 10 : power_dbm);
  return true;
}

bool ready() { return ok; }

bool transmit(const std::vector<gatecore::Pulse>& pulses) {
  if (!ok || pulses.empty()) return false;
  abort_capture();
  std::vector<rmt_item32_t> items;
  items.reserve(pulses.size() / 2 + 2);
  bool half = false;
  for (const auto& p : pulses) append(items, half, p.high, p.us);
  if (half) {
    // Close the last item with an end marker
    items.back().level1 = 0;
    items.back().duration1 = 0;
  } else {
    items.push_back(rmt_item32_t{});
  }

  ELECHOUSE_cc1101.SetTx();
  rmt_write_items(TX_CHANNEL, items.data(), int(items.size()), false);
  bool finished = rmt_wait_tx_done(TX_CHANNEL, pdMS_TO_TICKS(MAX_TX_MS)) == ESP_OK;
  if (!finished) {
    rmt_tx_stop(TX_CHANNEL);
    log_e("Transmission cut at %u ms", unsigned(MAX_TX_MS));
  }
  idle();
  ++transmissions;
  return finished;
}

bool start_capture(uint32_t ms) {
  if (!ok || listening) return false;
  edge_count = 0;
  edge_at = micros();
  ELECHOUSE_cc1101.SetRx();
  attachInterrupt(digitalPinToInterrupt(PIN_CC1101_GDO2), on_edge, CHANGE);
  listening = true;
  listen_until = millis() + ms;
  return true;
}

bool capturing() { return listening; }

bool capture_finished() {
  return listening && (int32_t(listen_until - millis()) <= 0 || edge_count >= CAPTURE_MAX_EDGES);
}

void abort_capture() {
  if (!listening) return;
  detachInterrupt(digitalPinToInterrupt(PIN_CC1101_GDO2));
  listening = false;
  idle();
}

std::vector<uint32_t> take_capture() {
  std::vector<uint32_t> out;
  abort_capture();
  size_t n = edge_count;
  size_t first = 0;
  // Start at the first HIGH so levels alternate from HIGH, as decode expects
  while (first < n && !edge_levels[first]) ++first;
  out.reserve(n - first);
  for (size_t i = first; i < n; ++i) out.push_back(edges[i]);
  return out;
}

std::vector<uint32_t> capture(uint32_t ms) {
  capture_rssi = 0;
  if (!start_capture(ms)) return {};
  int strongest = -200;
  while (!capture_finished()) {
    // A blocking capture may outlast the 10 s task watchdog
    esp_task_wdt_reset();
    delay(10);
    int rssi = ELECHOUSE_cc1101.getRssi();
    if (rssi > strongest) strongest = rssi;
  }
  capture_rssi = strongest > -200 ? strongest : 0;
  return take_capture();
}

int last_capture_rssi() { return capture_rssi; }

Diag diag() {
  Diag d;
  d.found = ok;
  d.frequency_mhz = tuned_mhz;
  d.power_dbm = tuned_dbm;
  if (ok) {
    d.partnum = ELECHOUSE_cc1101.SpiReadStatus(CC1101_PARTNUM);
    d.version = ELECHOUSE_cc1101.SpiReadStatus(CC1101_VERSION);
  }
  return d;
}

uint32_t tx_count() { return transmissions; }

}  // namespace radio
