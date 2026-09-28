// SHA-256 and HMAC-SHA256, portable C++ with no platform dependency, so the
// signature check runs the same on the ESP32 and in the host unit tests.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace gatecore {

class Sha256 {
 public:
  Sha256();
  void update(const uint8_t* data, size_t len);
  void update(const std::string& s) { update(reinterpret_cast<const uint8_t*>(s.data()), s.size()); }
  void finish(uint8_t out[32]);

 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t buf_[64];
  size_t buf_len_;
  uint64_t total_;
};

std::string to_hex(const uint8_t* data, size_t len);
std::string sha256_hex(const std::string& data);
std::string hmac_sha256_hex(const std::string& key, const std::string& message);

// Compares two strings in time independent of where they differ
bool constant_time_equal(const std::string& a, const std::string& b);

}  // namespace gatecore
