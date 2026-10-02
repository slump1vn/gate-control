// Settings kept in NVS (flash), written in setup mode and read at boot.
#pragma once

#include <Arduino.h>

#include "rf_frame.h"

enum Button : uint8_t { BUTTON_UP = 0, BUTTON_DOWN = 1, BUTTON_STOP = 2, BUTTON_COUNT = 3 };

const char* button_name(Button b);

struct ButtonCode {
  bool set = false;
  uint32_t code = 0;
  gatecore::RfProfile profile;
};

struct Config {
  String wifi_ssid;
  String wifi_password;
  // Backup WiFi, tried when the first one cannot be joined; empty = none
  String wifi2_ssid;
  String wifi2_password;
  // Static address on the primary WiFi, e.g. 172.87.80.254; empty = DHCP. The backup
  // network always uses DHCP, so a wrong static address can still be fixed from there
  String static_ip;
  String static_mask;
  String static_gw;
  String static_dns;
  uint32_t gate_id = 0;
  // The gate's controller token from /manage/gates: the HMAC key, never sent
  String secret;
  // Full heartbeat URL, e.g. http://192.168.2.80:8000/api/v1/gate/heartbeat/
  String heartbeat_url;
  String ntp_server = "pool.ntp.org";
  // WPA2 password of the setup-mode access point (printed on the device label)
  String setup_password;

  float frequency_mhz = 433.92f;
  int8_t power_dbm = 10;  // capped at +10 dBm whatever is stored
  uint8_t repeats = 10;   // frames per command, about 0.45 s at 350 us
  uint32_t ev1527_address = 0;  // this device's own 20-bit remote identity; 0 = not generated yet
  ButtonCode buttons[BUTTON_COUNT];

  // Validates and answers commands, but never transmits. On after provisioning.
  bool dry_run = true;

  bool static_ip_set() const { return static_ip.length() > 0; }

  bool provisioned() const { return wifi_ssid.length() && secret.length() && gate_id && heartbeat_url.length(); }
};

namespace config_store {

bool load(Config& cfg);
void save(const Config& cfg);
void erase();

// The last accepted command nonce, written on every accepted request so a
// reboot cannot reopen old nonces
uint64_t load_nonce();
void save_nonce(uint64_t nonce);

}  // namespace config_store
