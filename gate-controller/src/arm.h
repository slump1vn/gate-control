// Optional arm-position feedback from the controller's UP/DOWN LIMIT OUTPUT
// dry contacts (build with -DGATE_ARM_FEEDBACK=1). Without it every state is
// "unknown" and commands are reported as sent, never as confirmed.
#pragma once

#include <Arduino.h>

namespace arm {

// How long an open may take to reach the up limit before it is reported not confirmed
constexpr uint32_t CONFIRM_MS = 10000;

void begin();
bool enabled();
// "up", "down", "moving" or "unknown"
const char* state();
// An open was just transmitted: watch for the up limit
void expect_up();
// Called from the loop; returns a result ("open: ok" / "open: not_confirmed")
// once, when a watched open resolves, and nullptr otherwise
const char* poll();

}  // namespace arm
