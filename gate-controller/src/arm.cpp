#include "arm.h"

#include "pins.h"

#ifndef GATE_ARM_FEEDBACK
#define GATE_ARM_FEEDBACK 0
#endif

namespace arm {

namespace {
bool watching = false;
uint32_t watch_since = 0;
}  // namespace

void begin() {
#if GATE_ARM_FEEDBACK
  pinMode(PIN_LIMIT_UP, INPUT_PULLUP);
  pinMode(PIN_LIMIT_DOWN, INPUT_PULLUP);
#endif
}

bool enabled() { return GATE_ARM_FEEDBACK != 0; }

const char* state() {
#if GATE_ARM_FEEDBACK
  bool up = digitalRead(PIN_LIMIT_UP) == LOW;
  bool down = digitalRead(PIN_LIMIT_DOWN) == LOW;
  if (up && down) return "unknown";  // both limits at once: a wiring fault
  if (up) return "up";
  if (down) return "down";
  return "moving";
#else
  return "unknown";
#endif
}

void expect_up() {
  if (!enabled()) return;
  watching = true;
  watch_since = millis();
}

const char* poll() {
  if (!watching) return nullptr;
  if (strcmp(state(), "up") == 0) {
    watching = false;
    return "open: ok";
  }
  if (millis() - watch_since > CONFIRM_MS) {
    watching = false;
    return "open: not_confirmed";
  }
  return nullptr;
}

}  // namespace arm
