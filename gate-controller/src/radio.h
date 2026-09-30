// The CC1101 in asynchronous OOK mode: the ESP32's RMT peripheral drives GDO0
// with microsecond-exact pulses, so WiFi interrupts cannot stretch a bit.
#pragma once

#include <Arduino.h>

#include <vector>

#include "rf_frame.h"

namespace radio {

// Hard cap on one transmission, whatever is asked (design.md §3)
constexpr uint32_t MAX_TX_MS = 1500;

bool begin(float frequency_mhz, int8_t power_dbm);
bool ready();

// Send a pulse train and return the radio to idle. False if it was cut short
// by MAX_TX_MS or the radio is not ready. Blocks for the train's duration.
bool transmit(const std::vector<gatecore::Pulse>& pulses);

// Record edges on GDO2 for `ms` milliseconds, blocking (setup mode). Durations
// alternate starting with HIGH, as gatecore::decode expects.
std::vector<uint32_t> capture(uint32_t ms);

// The same without blocking, for a capture asked for from the admin UI while
// the device keeps serving commands: start, poll until done, take the result.
// A transmission aborts a capture in progress.
bool start_capture(uint32_t ms);
bool capturing();
// True once the capture window has ended (or the edge buffer is full)
bool capture_finished();
std::vector<uint32_t> take_capture();
void abort_capture();

uint32_t tx_count();

// What the setup page shows about the module
struct Diag {
  bool found = false;       // the CC1101 answered on SPI at boot
  uint8_t partnum = 0xFF;   // 0x00 on a CC1101
  uint8_t version = 0xFF;   // 0x14 or 0x04 on a CC1101
  float frequency_mhz = 0;
  int8_t power_dbm = 0;
};
Diag diag();

// Strongest signal (dBm) seen during the last blocking capture, or 0 if none was measured
int last_capture_rssi();

}  // namespace radio
