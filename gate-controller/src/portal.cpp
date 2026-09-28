#include "portal.h"

#include <WiFi.h>
#include <esp_random.h>
#include <esp_task_wdt.h>

#include "config.h"
#include "device.h"
#include "radio.h"
#include "rf_frame.h"

namespace portal {

namespace {

// Leave setup mode after this long without anyone using the page
const uint32_t IDLE_TIMEOUT_MS = 10UL * 60 * 1000;
const uint32_t CAPTURE_MS = 6000;
// Pairing: bursts sent while the receiver is in LEARN mode (about 3 s)
const int PAIR_BURSTS = 6;
// EV1527 button bits: a 4-button remote's A, B, C
const uint8_t PAIR_BUTTON_BITS[BUTTON_COUNT] = {0x8, 0x4, 0x2};

WebServer* srv = nullptr;
uint32_t last_use = 0;

String esc(const String& s) {
  String out;
  out.reserve(s.length());
  for (char c : s) {
    switch (c) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      case '\'': out += "&#39;"; break;
      default: out += c;
    }
  }
  return out;
}

bool parse_button(Button* out) {
  String b = srv->arg("button");
  for (int i = 0; i < BUTTON_COUNT; ++i) {
    if (b == button_name(Button(i))) {
      *out = Button(i);
      return true;
    }
  }
  return false;
}

void back(const String& msg) {
  srv->sendHeader("Location", "/?msg=" + msg);
  srv->send(303);
}

String button_row(Button b) {
  const ButtonCode& code = device::cfg.buttons[b];
  String name = button_name(b);
  String row = "<tr><td>" + name + "</td><td>";
  if (code.set) {
    row += "<code>" + String(gatecore::code_hex(code.code, code.profile.bits).c_str()) + "</code> (" +
           String(code.profile.bits) + " bit, " + String(code.profile.pulse_us) + " us)";
  } else {
    row += "&mdash;";
  }
  row += "</td><td>";
  for (const char* action : {"pair", "capture", "test"}) {
    row += "<form method=post action=/" + String(action) + "><input type=hidden name=button value=" + name +
           "><button>" + action + "</button></form> ";
  }
  return row + "</td></tr>";
}

void page() {
  last_use = millis();
  const Config& c = device::cfg;
  String msg = srv->arg("msg");
  String html;
  html.reserve(6000);
  html += F("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Gate controller setup</title><style>body{font:15px sans-serif;max-width:720px;margin:auto;padding:12px}"
            "input{width:100%;padding:6px;margin:2px 0 8px;box-sizing:border-box}td{padding:4px;vertical-align:top}"
            "form{display:inline}.msg{background:#fff3cd;padding:8px}</style>");
  html += "<h1>Gate controller setup</h1><p>Firmware " GATE_FW_VERSION ". Leaves setup mode after 10 minutes unused.</p>";
  if (msg.length()) html += "<p class=msg>" + esc(msg) + "</p>";

  html += F("<h2>Network and gate</h2><form method=post action=/save>");
  html += "WiFi SSID<input name=ssid value=\"" + esc(c.wifi_ssid) + "\">";
  html += "WiFi password (empty keeps it)<input name=wifipw type=password>";
  html += "Gate id (from /manage/gates)<input name=gate value=\"" + String(c.gate_id) + "\">";
  html += String("Controller token (empty keeps it") + (c.secret.length() ? ", set" : ", NOT SET") +
          ")<input name=secret type=password>";
  html += "Heartbeat URL<input name=hburl placeholder=http://192.168.2.80:8000/api/v1/gate/heartbeat/ value=\"" +
          esc(c.heartbeat_url) + "\">";
  html += "NTP server<input name=ntp value=\"" + esc(c.ntp_server) + "\">";
  html += "Frequency (MHz)<input name=freq value=\"" + String(c.frequency_mhz, 3) + "\">";
  html += "Frames per command<input name=repeats value=\"" + String(c.repeats) + "\">";
  html += F("<button>Save</button></form>");

  html += F("<h2>Remote buttons</h2><p><b>pair</b>: put the barrier receiver in LEARN mode first; the device then "
            "sends its own code. <b>capture</b>: hold the guard remote's button near the device. <b>test</b>: "
            "sends the stored code (the barrier moves).</p><table>");
  for (int b = 0; b < BUTTON_COUNT; ++b) html += button_row(Button(b));
  html += F("</table>");

  html += "<h2>Dry run: " + String(c.dry_run ? "ON (commands move nothing)" : "off") + "</h2>";
  html += "<form method=post action=/dryrun><input type=hidden name=on value=" + String(c.dry_run ? "0" : "1") +
          "><button>" + String(c.dry_run ? "Turn dry run off" : "Turn dry run on") + "</button></form>";
  html += F("<h2>Finish</h2><form method=post action=/exit><button>Save and restart</button></form>");
  srv->send(200, "text/html", html);
}

void save() {
  last_use = millis();
  Config& c = device::cfg;
  if (srv->hasArg("ssid")) c.wifi_ssid = srv->arg("ssid");
  if (srv->arg("wifipw").length()) c.wifi_password = srv->arg("wifipw");
  if (srv->hasArg("gate")) c.gate_id = strtoul(srv->arg("gate").c_str(), nullptr, 10);
  if (srv->arg("secret").length()) c.secret = srv->arg("secret");
  if (srv->hasArg("hburl")) c.heartbeat_url = srv->arg("hburl");
  if (srv->arg("ntp").length()) c.ntp_server = srv->arg("ntp");
  float freq = srv->arg("freq").toFloat();
  if (freq >= 433.05f && freq <= 434.79f) c.frequency_mhz = freq;
  long repeats = srv->arg("repeats").toInt();
  if (repeats >= 1 && repeats <= 30) c.repeats = uint8_t(repeats);
  config_store::save(c);
  back(c.provisioned() ? "Saved." : "Saved, but WiFi, gate id, token and heartbeat URL are all needed.");
}

void pair() {
  last_use = millis();
  Button b;
  if (!parse_button(&b)) return back("Unknown button");
  Config& c = device::cfg;
  if (!c.ev1527_address) {
    // This device's own remote identity, generated once
    do c.ev1527_address = esp_random() & 0xFFFFF; while (!c.ev1527_address);
  }
  ButtonCode& code = c.buttons[b];
  code.set = true;
  code.code = gatecore::ev1527_code(c.ev1527_address, PAIR_BUTTON_BITS[b]);
  code.profile = gatecore::RfProfile();
  config_store::save(c);
  for (int i = 0; i < PAIR_BURSTS; ++i) {
    esp_task_wdt_reset();
    device::transmit_button(b);
    delay(100);
  }
  back(String("Sent ") + button_name(b) + " code " + gatecore::code_hex(code.code, 24).c_str() +
       ". If the receiver learned it, press test.");
}

void capture() {
  last_use = millis();
  Button b;
  if (!parse_button(&b)) return back("Unknown button");
  esp_task_wdt_reset();
  std::vector<uint32_t> edges = radio::capture(CAPTURE_MS);
  esp_task_wdt_reset();
  gatecore::Decoded got = gatecore::decode_confirmed(edges);
  if (!got.ok) {
    return back(String("Nothing decoded twice in ") + edges.size() +
                " edges. Hold the remote button closer, for the whole 6 seconds.");
  }
  ButtonCode& code = device::cfg.buttons[b];
  code.set = true;
  code.code = got.code;
  code.profile = got.profile;
  config_store::save(device::cfg);
  back(String("Captured ") + button_name(b) + ": " + gatecore::code_hex(got.code, got.profile.bits).c_str());
}

void test() {
  last_use = millis();
  Button b;
  if (!parse_button(&b)) return back("Unknown button");
  if (!device::cfg.buttons[b].set) return back("No code stored for that button");
  back(device::transmit_button(b) ? String("Sent ") + button_name(b) : String("Transmission failed"));
}

void dry_run() {
  last_use = millis();
  device::cfg.dry_run = srv->arg("on") == "1";
  config_store::save(device::cfg);
  back(device::cfg.dry_run ? "Dry run on" : "Dry run off: commands now move the barrier");
}

void finish() {
  srv->send(200, "text/plain", "Restarting into normal mode");
  delay(500);
  ESP.restart();
}

void refuse_commands() {
  srv->send(503, "application/json", "{\"ok\":false,\"error\":\"setup_mode\"}");
}

}  // namespace

String begin(WebServer& server) {
  srv = &server;
  Config& c = device::cfg;
  if (c.setup_password.length() < 8) {
    // First boot: make a password and show it once on the USB console, for the device label
    const char* alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
    c.setup_password = "";
    for (int i = 0; i < 12; ++i) c.setup_password += alphabet[esp_random() % 31];
    config_store::save(c);
  }
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char name[20];
  snprintf(name, sizeof(name), "gate-ctl-%02x%02x", mac[4], mac[5]);
  WiFi.mode(WIFI_AP);
  WiFi.softAP(name, c.setup_password.c_str());
  Serial.printf("Setup mode: WiFi \"%s\", password \"%s\", open http://%s/\n", name, c.setup_password.c_str(),
                WiFi.softAPIP().toString().c_str());

  server.on("/", HTTP_GET, page);
  server.on("/save", HTTP_POST, save);
  server.on("/pair", HTTP_POST, pair);
  server.on("/capture", HTTP_POST, capture);
  server.on("/test", HTTP_POST, test);
  server.on("/dryrun", HTTP_POST, dry_run);
  server.on("/exit", HTTP_POST, finish);
  server.onNotFound(refuse_commands);
  server.begin();
  last_use = millis();
  return name;
}

void tick() {
  if (millis() - last_use > IDLE_TIMEOUT_MS) {
    Serial.println("Setup mode unused for 10 minutes: restarting");
    ESP.restart();
  }
}

}  // namespace portal
