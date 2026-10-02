#include "ota.h"

#include <HTTPClient.h>
#include <Preferences.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>

#include "device.h"
#include "net.h"

#ifdef GATE_BOARD_ESP32DEV
#define GATE_BOARD "esp32dev"
#else
#define GATE_BOARD "esp32s3"
#endif

// Found by the server in an uploaded image: which firmware and for which board
extern "C" const char GATE_FW_TAG[] = "GATEFW:" GATE_FW_VERSION ":" GATE_BOARD ":END";

// Arduino marks a new firmware valid at boot unless told to wait: we keep it on
// probation until a heartbeat gets through (see heartbeat_ok)
extern "C" bool verifyRollbackLater() { return true; }

namespace ota {

namespace {

enum State { IDLE, DOWNLOADING, FAILED, REBOOTING, VERIFYING, DONE, ROLLED_BACK };
const char* STATE_NAMES[] = {"idle", "downloading", "failed", "rebooting", "verifying", "done", "rolled_back"};

const char* NS = "gateota";
const uint32_t STALL_MS = 15000;

volatile State state = IDLE;
volatile uint8_t progress = 0;
volatile uint32_t job_id = 0;
const char* error_text = "";
String target_version;
bool reported_final = false;
bool on_probation = false;
uint32_t probation_started = 0;

String task_url, task_sha;
uint32_t task_size = 0;

void remember(uint32_t job, const String& version) {
  Preferences p;
  p.begin(NS, false);
  p.putULong("job", job);
  p.putString("target", version);
  p.end();
}

void forget() {
  Preferences p;
  p.begin(NS, false);
  p.clear();
  p.end();
}

void fail(const char* why) {
  error_text = why;
  state = FAILED;
  Serial.printf("Update failed: %s\n", why);
  net::heartbeat_soon();
}

String hex(const uint8_t* bytes, size_t n) {
  static const char digits[] = "0123456789abcdef";
  String out;
  out.reserve(n * 2);
  for (size_t i = 0; i < n; ++i) {
    out += digits[bytes[i] >> 4];
    out += digits[bytes[i] & 0xf];
  }
  return out;
}

void download(void*) {
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(STALL_MS);
  if (!http.begin(task_url)) {
    fail("bad_url");
    vTaskDelete(nullptr);
  }
  int status = http.GET();
  if (status != 200) {
    http.end();
    fail(status > 0 ? "http_error" : "server_unreachable");
    vTaskDelete(nullptr);
  }
  if (http.getSize() != int(task_size)) {
    http.end();
    fail("size_mismatch");
    vTaskDelete(nullptr);
  }
  // Writes to the app slot we are not running from
  if (!Update.begin(task_size, U_FLASH)) {
    http.end();
    fail("no_space");
    vTaskDelete(nullptr);
  }
  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);
  WiFiClient* stream = http.getStreamPtr();
  static uint8_t buf[2048];
  uint32_t done = 0, last_data = millis();
  while (done < task_size) {
    size_t avail = stream->available();
    if (!avail) {
      if (!http.connected() || millis() - last_data > STALL_MS) break;
      delay(5);
      continue;
    }
    size_t want = min(min(avail, sizeof(buf)), size_t(task_size - done));
    int n = stream->readBytes(buf, want);
    if (n <= 0) continue;
    mbedtls_sha256_update(&sha, buf, n);
    if (Update.write(buf, n) != size_t(n)) break;
    done += n;
    progress = uint8_t(uint64_t(done) * 100 / task_size);
    last_data = millis();
  }
  http.end();
  uint8_t digest[32];
  mbedtls_sha256_finish(&sha, digest);
  mbedtls_sha256_free(&sha);
  if (done < task_size) {
    Update.abort();
    fail("download_incomplete");
    vTaskDelete(nullptr);
  }
  // The hash came in the signed command: an image changed on the way is never booted
  if (hex(digest, 32) != task_sha) {
    Update.abort();
    fail("sha256_mismatch");
    vTaskDelete(nullptr);
  }
  // Checks the image (chip, segments) and makes it the next boot
  if (!Update.end()) {
    fail("image_rejected");
    vTaskDelete(nullptr);
  }
  remember(job_id, target_version);
  state = REBOOTING;
  Serial.printf("Update %s written, restarting\n", target_version.c_str());
  delay(1500);
  ESP.restart();
}

bool hex64(const String& s) {
  if (s.length() != 64) return false;
  for (char c : s) {
    if (!isdigit(c) && (c < 'a' || c > 'f')) return false;
  }
  return true;
}

}  // namespace

const char* board() { return GATE_BOARD; }

void begin() {
  Serial.println(GATE_FW_TAG);
  esp_ota_img_states_t img;
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (running && esp_ota_get_state_partition(running, &img) == ESP_OK && img == ESP_OTA_IMG_PENDING_VERIFY) {
    on_probation = true;
    probation_started = millis();
  }
  Preferences p;
  if (!p.begin(NS, true)) return;
  job_id = p.getULong("job", 0);
  target_version = p.getString("target", "");
  p.end();
  if (!job_id) return;
  if (target_version == GATE_FW_VERSION) {
    state = on_probation ? VERIFYING : DONE;
  } else {
    // We are the previous firmware again: the new one did not prove itself
    state = ROLLED_BACK;
    error_text = "rolled_back";
  }
}

void tick() {
  if (on_probation && millis() - probation_started > VERIFY_MS) {
    Serial.println("Update: no heartbeat accepted in time, rolling back");
    esp_ota_mark_app_invalid_rollback_and_reboot();
  }
}

bool start(const String& url, const String& sha256_hex, uint32_t size, const String& version, uint32_t job,
           const char** error) {
  if (state == DOWNLOADING || state == REBOOTING) return *error = "busy", false;
  if (on_probation) return *error = "verifying", false;
  if (!url.startsWith("http://") && !url.startsWith("https://")) return *error = "bad_url", false;
  if (!hex64(sha256_hex)) return *error = "bad_sha256", false;
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  if (!next) return *error = "no_ota_partition", false;
  if (size < 1024 || size > next->size) return *error = "bad_size", false;
  task_url = url;
  task_sha = sha256_hex;
  task_size = size;
  target_version = version;
  job_id = job;
  progress = 0;
  error_text = "";
  reported_final = false;
  state = DOWNLOADING;
  if (xTaskCreate(download, "ota", 8192, nullptr, 1, nullptr) != pdPASS) {
    state = FAILED;
    return *error = "no_memory", false;
  }
  return true;
}

void describe(JsonDocument& doc) {
  State s = state;
  if (s == IDLE) return;
  JsonObject u = doc["update"].to<JsonObject>();
  u["state"] = STATE_NAMES[s];
  if (job_id) u["job"] = job_id;
  if (target_version.length()) u["version"] = target_version;
  if (s == DOWNLOADING) u["progress"] = progress;
  if (error_text[0]) u["error"] = error_text;
  if (s == FAILED || s == DONE || s == ROLLED_BACK) reported_final = true;
}

void heartbeat_ok() {
  if (on_probation) {
    esp_ota_mark_app_valid_cancel_rollback();
    on_probation = false;
    Serial.println("Update: firmware confirmed");
    if (state == VERIFYING) state = DONE;
    net::heartbeat_soon();
    return;
  }
  if (reported_final) {
    // The server has heard how it ended
    forget();
    reported_final = false;
    state = IDLE;
    job_id = 0;
    target_version = "";
    error_text = "";
  }
}

}  // namespace ota
