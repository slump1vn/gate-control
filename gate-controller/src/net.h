// WiFi (with the recovery ladder of design.md §6), the clock, and the signed
// heartbeat to the LPR service.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include <functional>

#include "config.h"

namespace net {

void begin(const Config& cfg);
// From the loop: reconnect with backoff, restart the WiFi stack after 5 min
// offline, reboot after 15 min
void tick();
bool connected();
int rssi();

bool clock_synced();
int64_t now_s();

// Heartbeat every 10 s from its own task; `fill` adds the device's fields
void start_heartbeat(const Config& cfg, std::function<void(JsonDocument&)> fill);
// Send the next heartbeat now rather than at its turn (a capture result is waiting)
void heartbeat_soon();

// The path part of a URL ("http://host:8000/api/x/" -> "/api/x/")
String url_path(const String& url);

}  // namespace net
