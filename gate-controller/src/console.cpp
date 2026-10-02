#include "console.h"

#include <Arduino.h>
#include <ArduinoJson.h>

#include "config.h"
#include "device.h"

namespace console {

namespace {

const size_t MAX_LINE = 1024;
String line;

void answer(const String& text) { Serial.println("console: " + text); }

void show() {
  const Config& c = device::cfg;
  JsonDocument doc;
  doc["firmware"] = GATE_FW_VERSION;
  doc["ssid"] = c.wifi_ssid;
  doc["wifi_password_set"] = c.wifi_password.length() > 0;
  doc["ssid2"] = c.wifi2_ssid;
  doc["wifi2_password_set"] = c.wifi2_password.length() > 0;
  doc["ip"] = c.static_ip.length() ? c.static_ip : "dhcp";
  doc["mask"] = c.static_mask;
  doc["gw"] = c.static_gw;
  doc["dns"] = c.static_dns;
  doc["gate"] = c.gate_id;
  doc["secret_set"] = c.secret.length() > 0;
  doc["hburl"] = c.heartbeat_url;
  doc["ntp"] = c.ntp_server;
  doc["dry_run"] = c.dry_run;
  doc["repeats"] = c.repeats;
  doc["pulse_us"] = c.pulse_us;
  doc["provisioned"] = c.provisioned();
  String out;
  serializeJson(doc, out);
  answer(out);
}

bool valid_ip(const String& text) {
  IPAddress ip;
  return ip.fromString(text);
}

void set(JsonDocument& doc) {
  Config& c = device::cfg;
  int changed = 0;
  if (doc["ip"].is<const char*>()) {
    // {"ip":""} or {"ip":"dhcp"} goes back to DHCP; a static address needs mask and gateway too
    String ip = doc["ip"].as<const char*>();
    String mask = doc["mask"] | c.static_mask.c_str();
    String gw = doc["gw"] | c.static_gw.c_str();
    String dns = doc["dns"] | c.static_dns.c_str();
    if (ip.length() == 0 || ip == "dhcp") {
      c.static_ip = c.static_mask = c.static_gw = c.static_dns = "";
    } else if (!valid_ip(ip) || !valid_ip(mask) || !valid_ip(gw) || (dns.length() && !valid_ip(dns))) {
      return answer("error: ip, mask, gw (and dns) must be IPv4 addresses");
    } else {
      c.static_ip = ip;
      c.static_mask = mask;
      c.static_gw = gw;
      c.static_dns = dns;
    }
    ++changed;
  }
  if (doc["ssid"].is<const char*>()) c.wifi_ssid = doc["ssid"].as<const char*>(), ++changed;
  if (doc["wifipw"].is<const char*>()) c.wifi_password = doc["wifipw"].as<const char*>(), ++changed;
  if (doc["ssid2"].is<const char*>()) c.wifi2_ssid = doc["ssid2"].as<const char*>(), ++changed;
  if (doc["wifipw2"].is<const char*>()) c.wifi2_password = doc["wifipw2"].as<const char*>(), ++changed;
  if (doc["gate"].is<unsigned>()) c.gate_id = doc["gate"].as<unsigned>(), ++changed;
  if (doc["secret"].is<const char*>()) c.secret = doc["secret"].as<const char*>(), ++changed;
  if (doc["hburl"].is<const char*>()) c.heartbeat_url = doc["hburl"].as<const char*>(), ++changed;
  if (doc["ntp"].is<const char*>()) c.ntp_server = doc["ntp"].as<const char*>(), ++changed;
  if (doc["dry_run"].is<bool>()) c.dry_run = doc["dry_run"].as<bool>(), ++changed;
  if (doc["setuppw"].is<const char*>()) {
    // The setup access point's WPA2 password and the /ui login: WPA2 needs 8 to 63 characters
    String pw = doc["setuppw"].as<const char*>();
    if (pw.length() < 8 || pw.length() > 63) return answer("error: setuppw must be 8 to 63 characters");
    c.setup_password = pw;
    ++changed;
  }
  config_store::save(c);
  answer("saved " + String(changed) + " setting(s); provisioned=" + (c.provisioned() ? "yes" : "no") +
         (c.provisioned() ? "; restart to use them" : ""));
}

void run(const String& text) {
  JsonDocument doc;
  if (deserializeJson(doc, text)) return answer("error: not JSON");
  String cmd = doc["cmd"] | "";
  if (cmd == "show") return show();
  if (cmd == "set") return set(doc);
  if (cmd == "restart") {
    answer("restarting");
    Serial.flush();
    delay(200);
    ESP.restart();
  }
  answer("error: unknown cmd (show, set, restart)");
}

}  // namespace

void tick() {
  while (Serial.available()) {
    char ch = char(Serial.read());
    if (ch == '\n' || ch == '\r') {
      if (line.length()) {
        String text = line;
        line = "";
        text.trim();
        if (text.startsWith("{")) run(text);
      }
    } else if (line.length() < MAX_LINE) {
      line += ch;
    } else {
      line = "";
      answer("error: line too long");
    }
  }
}

}  // namespace console
