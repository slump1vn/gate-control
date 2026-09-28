#include "api.h"

#include <ArduinoJson.h>

#include "arm.h"
#include "config.h"
#include "device.h"
#include "net.h"
#include "radio.h"

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
  doc["stop_supported"] = device::cfg.buttons[BUTTON_STOP].set;
  doc["rf_tx_count"] = radio::tx_count();
  doc["uptime_s"] = device::uptime_s();
  doc["wifi_rssi"] = net::rssi();
  doc["last_command_result"] = device::last_result();
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
    return reply(200, doc);
  }

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
