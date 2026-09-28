// Setup mode: the device's own access point and a page to provision WiFi, the
// gate, the secret, pair with the receiver or capture a remote, test each
// button, and turn dry run off. No command API is served meanwhile.
#pragma once

#include <WebServer.h>

namespace portal {

// Access point up and page served; returns the AP name
String begin(WebServer& server);
// From the loop: leaves setup mode (reboots) after 10 minutes
void tick();

}  // namespace portal
