// Provisioning over the USB serial port, one JSON object per line. Reaching
// the port means holding the device, so this opens nothing on the network;
// it saves typing a 64-character token into the setup page on a phone.
//
//   {"cmd":"show"}                     settings, without secrets
//   {"cmd":"set","ssid":"...","wifipw":"...","ssid2":"...","wifipw2":"...",
//    "gate":1,"secret":"...",
//    "hburl":"http://host:8000/api/v1/gate/heartbeat/","ntp":"...",
//    "dry_run":true}                   any subset; saved to flash
//   {"cmd":"restart"}
//
// Every command answers with one line starting "console: ".
#pragma once

namespace console {

// From the loop, in either mode
void tick();

}  // namespace console
