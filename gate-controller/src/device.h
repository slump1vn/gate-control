// State shared by the command API, the setup portal and the heartbeat task.
#pragma once

#include <Arduino.h>

#include "config.h"
#include "guard.h"

#ifndef GATE_FW_VERSION
#define GATE_FW_VERSION "rf-dev"
#endif

namespace device {

extern Config cfg;
extern gatecore::Guard guard;

// The heartbeat task reads what the loop writes: hold this around both
void lock();
void unlock();

// "open: sent", "stop: dry_run", ...; reported in /status and the heartbeat
void set_last_result(const String& result);
String last_result();

// Send a button's stored code as one command's burst. False if the code is
// not set, the radio is not ready, or the transmission was cut short.
bool transmit_button(Button b);

uint32_t uptime_s();

}  // namespace device
