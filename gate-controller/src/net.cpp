#include "net.h"

#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_task_wdt.h>
#include <sys/time.h>

#include "guard.h"

namespace net {

namespace {

const uint32_t HEARTBEAT_MS = 10000;
const uint32_t HTTP_TIMEOUT_MS = 4000;
const uint32_t RESTART_WIFI_AFTER_MS = 5UL * 60 * 1000;
const uint32_t REBOOT_AFTER_MS = 15UL * 60 * 1000;
const uint32_t BACKOFF_MAX_MS = 30000;
// Any time before this is an unset clock (the ESP32 boots in 1970)
const int64_t VALID_EPOCH = 1700000000;

String ssid, password;
bool was_connected = false;
bool stack_restarted = false;
uint32_t offline_since = 0;
uint32_t next_attempt = 0;
uint32_t backoff = 1000;

volatile bool hb_soon = false;
const Config* hb_cfg = nullptr;
std::function<void(JsonDocument&)> hb_fill;
uint64_t hb_nonce = 0;

void set_clock(double epoch) {
  if (epoch < VALID_EPOCH) return;
  timeval tv;
  tv.tv_sec = time_t(epoch);
  tv.tv_usec = suseconds_t((epoch - double(tv.tv_sec)) * 1e6);
  settimeofday(&tv, nullptr);
  log_i("Clock set from the server: %lld", (long long)tv.tv_sec);
}

void send_heartbeat() {
  JsonDocument doc;
  doc["gate_id"] = hb_cfg->gate_id;
  doc["transport"] = "rf433";
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["clock_synced"] = clock_synced();
  hb_fill(doc);
  String body;
  serializeJson(doc, body);

  int64_t now = now_s();
  uint64_t candidate = clock_synced() ? uint64_t(now) * 1000 : 0;
  hb_nonce = candidate > hb_nonce ? candidate : hb_nonce + 1;
  String nonce = String((unsigned long long)hb_nonce);
  String ts = String((long long)now);
  std::string sig = gatecore::sign(hb_cfg->secret.c_str(), "POST", url_path(hb_cfg->heartbeat_url).c_str(),
                                   nonce.c_str(), ts.c_str(), body.c_str());

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  if (!http.begin(hb_cfg->heartbeat_url)) return;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Gate-Nonce", nonce);
  http.addHeader("X-Gate-Ts", ts);
  http.addHeader("X-Gate-Sig", sig.c_str());
  int status = http.POST(body);
  String reply = status > 0 ? http.getString() : String();
  http.end();

  JsonDocument answer;
  if (reply.length() && !deserializeJson(answer, reply) && answer["server_ts"].is<double>()) {
    // Either a normal reply, or 403 CLOCK_SKEW telling a device that just booted the time
    double server_ts = answer["server_ts"].as<double>();
    if (!clock_synced() || fabs(double(now_s()) - server_ts) > 5) set_clock(server_ts);
  }
  if (status != 200) log_w("Heartbeat: HTTP %d", status);
}

void heartbeat_task(void*) {
  esp_task_wdt_add(nullptr);
  for (;;) {
    esp_task_wdt_reset();
    hb_soon = false;
    if (connected()) send_heartbeat();
    // Wait out the interval in short steps, so heartbeat_soon() is heard
    for (uint32_t waited = 0; waited < HEARTBEAT_MS && !hb_soon; waited += 200) {
      esp_task_wdt_reset();
      vTaskDelay(pdMS_TO_TICKS(200));
    }
  }
}

}  // namespace

void begin(const Config& cfg) {
  ssid = cfg.wifi_ssid;
  password = cfg.wifi_password;
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(("gate-ctl-" + String(cfg.gate_id)).c_str());
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), password.c_str());
  offline_since = millis();
  configTime(0, 0, cfg.ntp_server.c_str(), "pool.ntp.org");
}

void tick() {
  uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED) {
    if (!was_connected) log_i("WiFi connected: %s, %d dBm", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    was_connected = true;
    stack_restarted = false;
    backoff = 1000;
    return;
  }
  if (was_connected) {
    log_w("WiFi lost");
    was_connected = false;
    offline_since = now;
    next_attempt = now;
  }
  uint32_t offline = now - offline_since;
  if (offline > REBOOT_AFTER_MS) {
    log_e("Offline for 15 min: rebooting");
    ESP.restart();
  }
  if (offline > RESTART_WIFI_AFTER_MS && !stack_restarted) {
    log_w("Offline for 5 min: restarting the WiFi stack");
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    delay(200);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), password.c_str());
    stack_restarted = true;
    next_attempt = now + BACKOFF_MAX_MS;
    return;
  }
  if (int32_t(now - next_attempt) >= 0) {
    WiFi.reconnect();
    next_attempt = now + backoff;
    backoff = backoff * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : backoff * 2;
  }
}

bool connected() { return WiFi.status() == WL_CONNECTED; }

int rssi() { return connected() ? WiFi.RSSI() : 0; }

bool clock_synced() { return int64_t(time(nullptr)) > VALID_EPOCH; }

int64_t now_s() { return int64_t(time(nullptr)); }

void start_heartbeat(const Config& cfg, std::function<void(JsonDocument&)> fill) {
  hb_cfg = &cfg;
  hb_fill = fill;
  xTaskCreatePinnedToCore(heartbeat_task, "heartbeat", 8192, nullptr, 1, nullptr, 0);
}

void heartbeat_soon() { hb_soon = true; }

String url_path(const String& url) {
  int scheme = url.indexOf("://");
  int from = scheme >= 0 ? scheme + 3 : 0;
  int slash = url.indexOf('/', from);
  if (slash < 0) return "/";
  int query = url.indexOf('?', slash);
  return query < 0 ? url.substring(slash) : url.substring(slash, query);
}

}  // namespace net
