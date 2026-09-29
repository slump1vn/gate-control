// Every rule a command must pass before the radio may be keyed, with no
// hardware in it: contract v2 signature, clock, timestamp window, nonce, the
// UP/DOWN interlock and the motion rate limit. The firmware wraps it; the
// host tests exercise it directly.
#pragma once

#include <cstdint>
#include <string>

namespace gatecore {

// Capture and SaveCode are the admin UI's remote capture (listen, then keep
// what was heard); they move nothing, so no interlock or rate limit applies.
enum class Command { Open, Close, Stop, Status, Capture, SaveCode, Unknown };

Command parse_command(const std::string& path);
const char* command_name(Command c);

struct Limits {
  uint32_t interlock_ms = 1000;          // open <-> close within this is refused
  uint32_t min_command_interval_ms = 3000;  // one motion command per this; stop exempt
  uint32_t ts_window_s = 30;             // |device time - ts| must be within this
};

struct Request {
  std::string method;  // "POST" / "GET"
  std::string path;    // "/open"
  std::string nonce;   // X-Gate-Nonce, as sent
  std::string ts;      // X-Gate-Ts, as sent
  std::string signature;  // X-Gate-Sig
  std::string body;
};

struct Verdict {
  int status;         // HTTP status: 200 when allowed
  const char* error;  // machine-readable reason when refused
  bool ok() const { return status == 200; }
};

// The canonical string v2 signs (same as lpr_app/utils/gate_signing.py)
std::string canonical(const std::string& method, const std::string& path, const std::string& nonce,
                      const std::string& ts, const std::string& body);
std::string sign(const std::string& secret, const std::string& method, const std::string& path,
                 const std::string& nonce, const std::string& ts, const std::string& body);

class Guard {
 public:
  explicit Guard(const Limits& limits = Limits()) : limits_(limits) {}

  // The last nonce accepted, restored from flash at boot
  void restore_nonce(uint64_t nonce) { last_nonce_ = nonce; }
  uint64_t last_nonce() const { return last_nonce_; }

  // Checks authenticity and freshness; on success the nonce is consumed.
  // now_s is device wall-clock time, meaningful only when clock_synced.
  Verdict authenticate(const std::string& secret, const Request& req, bool clock_synced, int64_t now_s);

  // Checks the interlock and rate limit for an authenticated command; on
  // success the motion is recorded. now_ms is a monotonic millisecond clock.
  Verdict admit(Command cmd, uint32_t now_ms);

 private:
  Limits limits_;
  uint64_t last_nonce_ = 0;
  Command last_motion_ = Command::Unknown;
  uint32_t last_motion_ms_ = 0;
  bool any_motion_ = false;
};

bool parse_u64(const std::string& s, uint64_t* out);
bool parse_i64(const std::string& s, int64_t* out);

}  // namespace gatecore
