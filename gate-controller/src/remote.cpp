#include "remote.h"

#include "device.h"
#include "net.h"
#include "radio.h"
#include "rf_frame.h"

namespace remote {

namespace {

// idle -> capturing -> captured | nothing | aborted ; captured -> saved
String state = "idle";
Button button = BUTTON_UP;
uint32_t job = 0;
uint32_t finished_ms = 0;
size_t edges = 0;
int frames = 0;
gatecore::Decoded candidate;
bool have_candidate = false;

void finish(const char* new_state) {
  device::lock();
  state = new_state;
  finished_ms = millis();
  device::unlock();
  net::heartbeat_soon();
}

}  // namespace

bool start(Button b, uint32_t seconds, uint32_t job_id) {
  if (radio::capturing()) return false;
  if (seconds < MIN_SECONDS) seconds = MIN_SECONDS;
  if (seconds > MAX_SECONDS) seconds = MAX_SECONDS;
  if (!radio::start_capture(seconds * 1000)) return false;
  device::lock();
  state = "capturing";
  button = b;
  job = job_id;
  have_candidate = false;
  edges = 0;
  frames = 0;
  device::unlock();
  return true;
}

void poll() {
  if (state == "capturing" && !radio::capturing()) {
    // The radio stopped listening without us: a command took it
    finish("aborted");
    return;
  }
  if (!radio::capture_finished()) return;
  std::vector<uint32_t> captured = radio::take_capture();
  std::vector<gatecore::Decoded> all = gatecore::decode_all(captured);
  gatecore::Decoded got = gatecore::decode_confirmed(captured);
  device::lock();
  edges = captured.size();
  frames = int(all.size());
  candidate = got;
  have_candidate = got.ok;
  device::unlock();
  finish(got.ok ? "captured" : "nothing");
}

void interrupted() {
  if (radio::capturing()) {
    radio::abort_capture();
    finish("aborted");
  }
}

bool save(Button b, const char** error) {
  if (!have_candidate || state != "captured") {
    *error = "no_capture";
    return false;
  }
  if (b != button) {
    *error = "captured_for_another_button";
    return false;
  }
  if (millis() - finished_ms > CANDIDATE_TTL_MS) {
    *error = "capture_expired";
    return false;
  }
  // The heartbeat task reads the buttons: change them under the lock
  device::lock();
  ButtonCode& code = device::cfg.buttons[b];
  code.set = true;
  code.code = candidate.code;
  code.profile = candidate.profile;
  state = "saved";
  have_candidate = false;
  device::unlock();
  config_store::save(device::cfg);
  net::heartbeat_soon();
  return true;
}

void describe(JsonDocument& doc) {
  device::lock();
  JsonObject cap = doc["capture"].to<JsonObject>();
  cap["state"] = state;
  cap["button"] = button_name(button);
  if (job) cap["job"] = job;
  if (state != "idle" && state != "capturing") {
    cap["age_s"] = (millis() - finished_ms) / 1000;
    cap["edges"] = edges;
    cap["frames"] = frames;
  }
  if (have_candidate) {
    cap["fingerprint"] = gatecore::code_fingerprint(candidate.code, candidate.profile.bits).c_str();
    cap["bits"] = candidate.profile.bits;
    cap["pulse_us"] = candidate.profile.pulse_us;
  }
  JsonObject buttons = doc["buttons"].to<JsonObject>();
  for (int b = 0; b < BUTTON_COUNT; ++b) {
    const ButtonCode& code = device::cfg.buttons[b];
    JsonObject entry = buttons[button_name(Button(b))].to<JsonObject>();
    entry["set"] = code.set;
    if (code.set) {
      entry["fingerprint"] = gatecore::code_fingerprint(code.code, code.profile.bits).c_str();
      entry["bits"] = code.profile.bits;
      entry["pulse_us"] = code.profile.pulse_us;
    }
  }
  device::unlock();
}

}  // namespace remote
