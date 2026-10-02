#include "config.h"

#include <Preferences.h>

namespace {

const char* NS = "gatectl";
const char* NONCE_NS = "gatenonce";

void save_button(Preferences& p, Button b, const ButtonCode& code) {
  String prefix = String("b") + int(b);
  p.putBool((prefix + "set").c_str(), code.set);
  p.putULong((prefix + "code").c_str(), code.code);
  p.putBytes((prefix + "prof").c_str(), &code.profile, sizeof(code.profile));
}

void load_button(Preferences& p, Button b, ButtonCode& code) {
  String prefix = String("b") + int(b);
  code.set = p.getBool((prefix + "set").c_str(), false);
  code.code = p.getULong((prefix + "code").c_str(), 0);
  if (p.getBytesLength((prefix + "prof").c_str()) == sizeof(code.profile)) {
    p.getBytes((prefix + "prof").c_str(), &code.profile, sizeof(code.profile));
  }
}

}  // namespace

const char* button_name(Button b) {
  switch (b) {
    case BUTTON_UP: return "up";
    case BUTTON_DOWN: return "down";
    case BUTTON_STOP: return "stop";
    default: return "?";
  }
}

namespace config_store {

bool load(Config& cfg) {
  Preferences p;
  if (!p.begin(NS, true)) return false;
  cfg.wifi_ssid = p.getString("ssid", "");
  cfg.wifi_password = p.getString("wifipw", "");
  cfg.wifi2_ssid = p.getString("ssid2", "");
  cfg.wifi2_password = p.getString("wifipw2", "");
  cfg.static_ip = p.getString("ip", "");
  cfg.static_mask = p.getString("mask", "");
  cfg.static_gw = p.getString("gw", "");
  cfg.static_dns = p.getString("dns", "");
  cfg.gate_id = p.getULong("gate", 0);
  cfg.secret = p.getString("secret", "");
  cfg.heartbeat_url = p.getString("hburl", "");
  cfg.ntp_server = p.getString("ntp", "pool.ntp.org");
  cfg.setup_password = p.getString("setuppw", "");
  cfg.frequency_mhz = p.getFloat("freq", 433.92f);
  cfg.power_dbm = p.getChar("power", 10);
  cfg.repeats = p.getUChar("repeats", 10);
  cfg.ev1527_address = p.getULong("addr", 0);
  cfg.dry_run = p.getBool("dryrun", true);
  for (int b = 0; b < BUTTON_COUNT; ++b) load_button(p, Button(b), cfg.buttons[b]);
  p.end();
  if (cfg.power_dbm > 10) cfg.power_dbm = 10;
  if (cfg.repeats < 1 || cfg.repeats > 30) cfg.repeats = 10;
  return true;
}

void save(const Config& cfg) {
  Preferences p;
  p.begin(NS, false);
  p.putString("ssid", cfg.wifi_ssid);
  p.putString("wifipw", cfg.wifi_password);
  p.putString("ssid2", cfg.wifi2_ssid);
  p.putString("wifipw2", cfg.wifi2_password);
  p.putString("ip", cfg.static_ip);
  p.putString("mask", cfg.static_mask);
  p.putString("gw", cfg.static_gw);
  p.putString("dns", cfg.static_dns);
  p.putULong("gate", cfg.gate_id);
  p.putString("secret", cfg.secret);
  p.putString("hburl", cfg.heartbeat_url);
  p.putString("ntp", cfg.ntp_server);
  p.putString("setuppw", cfg.setup_password);
  p.putFloat("freq", cfg.frequency_mhz);
  p.putChar("power", cfg.power_dbm > 10 ? 10 : cfg.power_dbm);
  p.putUChar("repeats", cfg.repeats);
  p.putULong("addr", cfg.ev1527_address);
  p.putBool("dryrun", cfg.dry_run);
  for (int b = 0; b < BUTTON_COUNT; ++b) save_button(p, Button(b), cfg.buttons[b]);
  p.end();
}

void erase() {
  Preferences p;
  p.begin(NS, false);
  p.clear();
  p.end();
}

uint64_t load_nonce() {
  Preferences p;
  if (!p.begin(NONCE_NS, true)) return 0;
  uint64_t n = p.getULong64("last", 0);
  p.end();
  return n;
}

void save_nonce(uint64_t nonce) {
  Preferences p;
  p.begin(NONCE_NS, false);
  p.putULong64("last", nonce);
  p.end();
}

}  // namespace config_store
