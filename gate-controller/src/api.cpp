#include "api.h"

#include <ArduinoJson.h>

#include "arm.h"
#include "config.h"
#include "device.h"
#include "net.h"
#include "ota.h"
#include "radio.h"
#include "remote.h"

using gatecore::Command;

namespace api {

namespace {

WebServer* srv = nullptr;

void reply(int status, JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  srv->send(status, "application/json", out);
}

void refuse(int status, const char* error) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = error;
  reply(status, doc);
}

void add_state(JsonDocument& doc) {
  doc["arm_state"] = arm::state();
  doc["dry_run"] = device::cfg.dry_run;
  doc["clock_synced"] = net::clock_synced();
  doc["transport"] = "rf433";
  doc["firmware_version"] = GATE_FW_VERSION;
  doc["board"] = ota::board();
  doc["stop_supported"] = device::cfg.buttons[BUTTON_STOP].set;
  doc["rf_tx_count"] = radio::tx_count();
  doc["uptime_s"] = device::uptime_s();
  doc["wifi_rssi"] = net::rssi();
  doc["last_command_result"] = device::last_result();
}

bool parse_button(const String& name, Button* out) {
  for (int b = 0; b < BUTTON_COUNT; ++b) {
    if (name == button_name(Button(b))) {
      *out = Button(b);
      return true;
    }
  }
  return false;
}

// POST /capture {"button": "up", "seconds": 10, "job": 12}: listen, answer at once
void handle_capture(const gatecore::Request& req) {
  JsonDocument body;
  Button button;
  if (deserializeJson(body, req.body) || !parse_button(body["button"] | "", &button)) {
    return refuse(400, "bad_request");
  }
  if (!remote::start(button, body["seconds"] | 10, body["job"] | 0)) return refuse(409, "busy");
  JsonDocument doc;
  doc["ok"] = true;
  doc["command"] = "capture";
  doc["result"] = "capturing";
  reply(202, doc);
}

// POST /capture/save {"button": "up"}: keep the last capture as that button's code
void handle_save(const gatecore::Request& req) {
  JsonDocument body;
  Button button;
  if (deserializeJson(body, req.body) || !parse_button(body["button"] | "", &button)) {
    return refuse(400, "bad_request");
  }
  const char* error = "";
  if (!remote::save(button, &error)) return refuse(409, error);
  device::set_last_result(String("save_code: ") + button_name(button));
  JsonDocument doc;
  doc["ok"] = true;
  doc["command"] = "save_code";
  doc["result"] = "saved";
  remote::describe(doc);
  reply(200, doc);
}

// POST /update {"path": "/api/v1/gate/firmware/download/<token>/", "sha256": "...",
//               "size": 1071376, "version": "rf-abc", "job": 12}
// The image comes from the server the heartbeat goes to; answer at once,
// progress and outcome come back in the heartbeat.
void handle_update(const gatecore::Request& req) {
  JsonDocument body;
  if (deserializeJson(body, req.body)) return refuse(400, "bad_request");
  String path = body["path"] | "";
  if (!path.startsWith("/")) return refuse(400, "bad_path");
  const String& hb = device::cfg.heartbeat_url;
  int host_end = hb.indexOf('/', hb.indexOf("//") + 2);
  String url = (host_end > 0 ? hb.substring(0, host_end) : hb) + path;
  const char* error = "";
  if (!ota::start(url, body["sha256"] | "", body["size"] | 0u, body["version"] | "", body["job"] | 0u,
                  &error)) {
    return refuse(409, error);
  }
  device::set_last_result(String("update: ") + (body["version"] | ""));
  JsonDocument doc;
  doc["ok"] = true;
  doc["command"] = "update";
  doc["result"] = "downloading";
  reply(202, doc);
}

Button button_for(Command cmd) {
  switch (cmd) {
    case Command::Open: return BUTTON_UP;
    case Command::Close: return BUTTON_DOWN;
    default: return BUTTON_STOP;
  }
}

void handle() {
  Command cmd = gatecore::parse_command(srv->uri().c_str());
  if (cmd == Command::Unknown) return refuse(404, "unknown_command");
  bool is_status = cmd == Command::Status;
  if (srv->method() != (is_status ? HTTP_GET : HTTP_POST)) return refuse(405, "method_not_allowed");

  gatecore::Request req;
  req.method = is_status ? "GET" : "POST";
  req.path = srv->uri().c_str();
  req.nonce = srv->header("X-Gate-Nonce").c_str();
  req.ts = srv->header("X-Gate-Ts").c_str();
  req.signature = srv->header("X-Gate-Sig").c_str();
  req.body = srv->hasArg("plain") ? srv->arg("plain").c_str() : "";

  gatecore::Verdict v = device::guard.authenticate(device::cfg.secret.c_str(), req, net::clock_synced(), net::now_s());
  if (!v.ok()) return refuse(v.status, v.error);
  // Persist before acting, so a reboot mid-command cannot reopen this nonce
  config_store::save_nonce(device::guard.last_nonce());

  JsonDocument doc;
  if (is_status) {
    doc["ok"] = true;
    add_state(doc);
    remote::describe(doc);
    return reply(200, doc);
  }
  if (cmd == Command::Capture) return handle_capture(req);
  if (cmd == Command::SaveCode) return handle_save(req);
  if (cmd == Command::Update) return handle_update(req);

  Button button = button_for(cmd);
  if (!device::cfg.buttons[button].set) {
    // No code for this button (a remote without STOP, or not paired yet)
    return refuse(501, cmd == Command::Stop ? "stop_not_supported" : "button_not_paired");
  }
  v = device::guard.admit(cmd, millis());
  if (!v.ok()) return refuse(v.status, v.error);

  const char* result;
  if (device::cfg.dry_run) {
    result = "dry_run";
    log_i("Dry run: would transmit %s", gatecore::command_name(cmd));
  } else if (!radio::ready()) {
    return refuse(503, "radio_unavailable");
  } else if (!device::transmit_button(button)) {
    device::set_last_result(String(gatecore::command_name(cmd)) + ": tx_failed");
    return refuse(500, "tx_failed");
  } else {
    // Radio is one-way: whether the barrier heard it is not known here
    result = "sent";
    if (cmd == Command::Open) arm::expect_up();
  }
  device::set_last_result(String(gatecore::command_name(cmd)) + ": " + result);

  doc["ok"] = true;
  doc["command"] = gatecore::command_name(cmd);
  doc["result"] = result;
  add_state(doc);
  reply(200, doc);
}

}  // namespace

void begin(WebServer& server) {
  srv = &server;
  static const char* headers[] = {"X-Gate-Nonce", "X-Gate-Ts", "X-Gate-Sig"};
  server.collectHeaders(headers, 3);
  server.onNotFound(handle);
  server.begin();
}

}  // namespace api
