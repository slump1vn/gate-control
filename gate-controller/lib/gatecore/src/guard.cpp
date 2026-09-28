#include "guard.h"

#include "crypto.h"

namespace gatecore {

Command parse_command(const std::string& path) {
  if (path == "/open") return Command::Open;
  if (path == "/close") return Command::Close;
  if (path == "/stop") return Command::Stop;
  if (path == "/status") return Command::Status;
  return Command::Unknown;
}

const char* command_name(Command c) {
  switch (c) {
    case Command::Open: return "open";
    case Command::Close: return "close";
    case Command::Stop: return "stop";
    case Command::Status: return "status";
    default: return "unknown";
  }
}

std::string canonical(const std::string& method, const std::string& path, const std::string& nonce,
                      const std::string& ts, const std::string& body) {
  std::string upper = method;
  for (auto& ch : upper) {
    if (ch >= 'a' && ch <= 'z') ch = char(ch - 'a' + 'A');
  }
  return upper + "\n" + path + "\n" + nonce + "\n" + ts + "\n" + sha256_hex(body);
}

std::string sign(const std::string& secret, const std::string& method, const std::string& path,
                 const std::string& nonce, const std::string& ts, const std::string& body) {
  return hmac_sha256_hex(secret, canonical(method, path, nonce, ts, body));
}

bool parse_u64(const std::string& s, uint64_t* out) {
  if (s.empty() || s.size() > 19) return false;
  uint64_t v = 0;
  for (char ch : s) {
    if (ch < '0' || ch > '9') return false;
    v = v * 10 + uint64_t(ch - '0');
  }
  *out = v;
  return true;
}

bool parse_i64(const std::string& s, int64_t* out) {
  // Timestamps are whole seconds; a fraction (as a v1 client sent) is ignored
  std::string whole = s.substr(0, s.find('.'));
  uint64_t v = 0;
  if (!parse_u64(whole, &v)) return false;
  *out = int64_t(v);
  return true;
}

static std::string lower(std::string s) {
  for (auto& ch : s) {
    if (ch >= 'A' && ch <= 'F') ch = char(ch - 'A' + 'a');
  }
  return s;
}

Verdict Guard::authenticate(const std::string& secret, const Request& req, bool clock_synced, int64_t now_s) {
  if (secret.empty() || req.signature.empty()) return {401, "unauthorized"};
  uint64_t nonce = 0;
  int64_t ts = 0;
  if (!parse_u64(req.nonce, &nonce) || !parse_i64(req.ts, &ts)) return {401, "unauthorized"};
  std::string expected = sign(secret, req.method, req.path, req.nonce, req.ts, req.body);
  if (!constant_time_equal(expected, lower(req.signature))) return {401, "unauthorized"};
  // Past this point the request is known to come from a holder of the secret
  if (!clock_synced) return {503, "clock_not_synced"};
  int64_t skew = now_s - ts;
  if (skew < 0) skew = -skew;
  if (skew > int64_t(limits_.ts_window_s)) return {401, "stale_timestamp"};
  if (nonce <= last_nonce_) return {409, "replayed_nonce"};
  last_nonce_ = nonce;
  return {200, ""};
}

Verdict Guard::admit(Command cmd, uint32_t now_ms) {
  if (cmd == Command::Stop || cmd == Command::Status) return {200, ""};
  if (cmd != Command::Open && cmd != Command::Close) return {404, "unknown_command"};
  if (any_motion_) {
    uint32_t since = now_ms - last_motion_ms_;  // wraps correctly across millis() overflow
    if (cmd != last_motion_ && since < limits_.interlock_ms) return {409, "interlock"};
    if (since < limits_.min_command_interval_ms) return {429, "rate_limited"};
  }
  any_motion_ = true;
  last_motion_ = cmd;
  last_motion_ms_ = now_ms;
  return {200, ""};
}

}  // namespace gatecore
