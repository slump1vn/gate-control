// The device's own web page: status and connection checks (CC1101, WiFi,
// server), listening for a remote, pairing or copying its buttons, trying the
// gate, and provisioning (primary and backup WiFi, gate, token).
//
// Setup mode: at / on the device's own access point, no command API meanwhile.
// Normal mode: at /ui/ on the device's WiFi address, behind HTTP Basic auth
// (admin / the setup password on the device label), next to the command API.
#pragma once

#include <WebServer.h>

namespace portal {

// Setup mode: access point up and page served; returns the AP name
String begin(WebServer& server);
// Normal mode: the page at /ui/, registered before api::begin
void begin_status(WebServer& server);
// From the loop: leaves setup mode (reboots) after 10 minutes unused
void tick();

}  // namespace portal
