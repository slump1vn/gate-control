// Firmware update over WiFi, asked for from the admin UI (through the gate
// agent, as a signed POST /update). The device downloads the image from the
// LPR service itself, checks its SHA-256 against the one in the signed
// command, writes it to the other app slot and restarts into it.
//
// The new firmware is on probation: it must get a heartbeat through (HTTP 200,
// i.e. WiFi, clock and token all work) within VERIFY_MS, or it rolls back to
// the previous one. A crash or watchdog reset before that also rolls back,
// done by the bootloader. The outcome is reported in the heartbeat.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace ota {

constexpr uint32_t VERIFY_MS = 5UL * 60 * 1000;

// Board name built into the image and reported in the heartbeat, so the
// server never offers an image built for another board
const char* board();

// At boot, before the network: learns whether this firmware is on probation
// and what an update started before the restart came to
void begin();
// From the loop: rolls back a firmware still on probation after VERIFY_MS
void tick();

// Start downloading `url` in the background. False (with the reason) when an
// update is already running or the request is unusable.
bool start(const String& url, const String& sha256_hex, uint32_t size, const String& version, uint32_t job,
           const char** error);

// The heartbeat's "update" object (nothing when no update is in progress)
void describe(JsonDocument& doc);
// The server accepted a heartbeat: the firmware works, keep it
void heartbeat_ok();

}  // namespace ota
