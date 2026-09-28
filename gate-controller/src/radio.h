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

// Record edges on GDO2 for `ms` milliseconds (setup mode only). Durations
// alternate starting with HIGH, as gatecore::decode expects.
std::vector<uint32_t> capture(uint32_t ms);

uint32_t tx_count();

}  // namespace radio
