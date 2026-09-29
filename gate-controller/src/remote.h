// A remote's code captured on request from the admin UI (through the gate
// agent), and kept on request. The code itself never leaves the device: what
// is reported is a fingerprint, the bit count and the pulse width.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include "config.h"

namespace remote {

// A capture waits this long for the admin to keep it
constexpr uint32_t CANDIDATE_TTL_MS = 10UL * 60 * 1000;
constexpr uint32_t MIN_SECONDS = 2;
constexpr uint32_t MAX_SECONDS = 15;

// Start listening for `seconds`; false if a capture is already running
bool start(Button button, uint32_t seconds, uint32_t job);
// From the loop: finishes a capture whose window has ended
void poll();
// A command is about to transmit: a capture in progress is abandoned
void interrupted();
// Keep the last capture as `button`'s code. False (with the reason) when
// there is no fresh capture for that button.
bool save(Button button, const char** error);

// The heartbeat's "capture" and "buttons" objects
void describe(JsonDocument& doc);

}  // namespace remote
