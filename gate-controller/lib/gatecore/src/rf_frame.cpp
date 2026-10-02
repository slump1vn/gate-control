#include "rf_frame.h"

#include <cstdio>

#include "crypto.h"

namespace gatecore {

namespace {

void push(std::vector<Pulse>& out, bool high, uint32_t us) {
  // Merge a level into the previous one if it continues it
  if (!out.empty() && out.back().high == high) {
    out.back().us += us;
  } else {
    out.push_back({high, us});
  }
}

bool near(uint32_t value, uint32_t expected, int tolerance_pct) {
  uint32_t margin = expected * uint32_t(tolerance_pct) / 100;
  return value + margin >= expected && value <= expected + margin;
}

uint8_t ratio(uint32_t value, uint32_t unit) { return uint8_t((value + unit / 2) / unit); }

}  // namespace

std::vector<Pulse> encode_frame(uint32_t code, const RfProfile& p) {
  std::vector<Pulse> out;
  out.reserve(size_t(p.bits) * 2 + 2);
  for (int i = p.bits - 1; i >= 0; --i) {
    bool one = (code >> i) & 1u;
    push(out, true, (one ? p.one_high : p.zero_high) * p.pulse_us);
    push(out, false, (one ? p.one_low : p.zero_low) * p.pulse_us);
  }
  push(out, true, p.sync_high * p.pulse_us);
  push(out, false, p.sync_low * p.pulse_us);
  return out;
}

std::vector<Pulse> encode_burst(uint32_t code, const RfProfile& p, int repeats) {
  std::vector<Pulse> frame = encode_frame(code, p);
  std::vector<Pulse> out;
  out.reserve(frame.size() * size_t(repeats > 0 ? repeats : 0));
  for (int r = 0; r < repeats; ++r) {
    for (const auto& pulse : frame) push(out, pulse.high, pulse.us);
  }
  return out;
}

std::vector<Pulse> encode_press(uint32_t code, const RfProfile& p, int repeats, uint32_t gap_us) {
  std::vector<Pulse> out;
  if (repeats <= 0) return out;
  std::vector<Pulse> frame = encode_frame(code, p);
  out.reserve(frame.size() * size_t(repeats) + 3);
  push(out, false, gap_us);
  for (int r = 0; r < repeats; ++r) {
    // The frame's own sync goes first, then its bits
    push(out, frame[frame.size() - 2].high, frame[frame.size() - 2].us);
    push(out, frame.back().high, frame.back().us);
    for (size_t i = 0; i + 2 < frame.size(); ++i) push(out, frame[i].high, frame[i].us);
  }
  push(out, true, p.sync_high * p.pulse_us);
  push(out, false, p.sync_low * p.pulse_us + gap_us);
  return out;
}

uint32_t duration_us(const std::vector<Pulse>& pulses) {
  uint32_t total = 0;
  for (const auto& p : pulses) total += p.us;
  return total;
}

uint32_t ev1527_code(uint32_t address, uint8_t button_bits) {
  return ((address & 0xFFFFFu) << 4) | (button_bits & 0x0Fu);
}

namespace {

// durations[i] is HIGH for even i and LOW for odd i. A sync is a LOW at least
// ten times its HIGH.
bool is_sync(const std::vector<uint32_t>& d, size_t low) {
  return low % 2 == 1 && low < d.size() && d[low] >= 10 * d[low - 1];
}

// The frame whose data starts at `start` (just after a sync) and whose own
// sync pair starts at `end`
Decoded decode_frame(const std::vector<uint32_t>& d, size_t start, size_t end, int tolerance_pct) {
  Decoded result;
  size_t pairs = (end - start) / 2;
  if (pairs < 8 || pairs > 32) return result;

  // T: the mean short pulse of the data pairs
  uint64_t short_sum = 0;
  for (size_t j = start; j < end; j += 2) short_sum += d[j] < d[j + 1] ? d[j] : d[j + 1];
  uint32_t unit = uint32_t(short_sum / pairs);
  if (unit == 0) return result;

  uint32_t code = 0;
  uint8_t long_ratio = 0;
  for (size_t j = start; j < end; j += 2) {
    bool one = d[j] > d[j + 1];
    uint32_t shorter = one ? d[j + 1] : d[j];
    uint32_t longer = one ? d[j] : d[j + 1];
    uint8_t r = ratio(longer, unit);
    if (r < 2) return result;
    if (long_ratio == 0) long_ratio = r;
    if (!near(shorter, unit, tolerance_pct) || !near(longer, unit * long_ratio, tolerance_pct)) return result;
    code = (code << 1) | (one ? 1u : 0u);
  }

  result.ok = true;
  result.code = code;
  result.profile.pulse_us = unit;
  result.profile.zero_high = 1;
  result.profile.zero_low = long_ratio;
  result.profile.one_high = long_ratio;
  result.profile.one_low = 1;
  result.profile.sync_high = ratio(d[end], unit) ? ratio(d[end], unit) : 1;
  result.profile.sync_low = ratio(d[end + 1], unit);
  result.profile.bits = uint8_t(pairs);
  return result;
}

}  // namespace

std::vector<Decoded> decode_all(const std::vector<uint32_t>& d, int tolerance_pct) {
  // Each frame lies between two syncs; interference can spoil some of a burst
  std::vector<Decoded> frames;
  size_t start = 0;
  for (size_t i = 1; i < d.size(); i += 2) {
    if (!is_sync(d, i)) continue;
    if (start != 0) {
      Decoded frame = decode_frame(d, start, i - 1, tolerance_pct);
      if (frame.ok) frames.push_back(frame);
    }
    start = i + 1;
  }
  return frames;
}

Decoded decode(const std::vector<uint32_t>& d, int tolerance_pct) {
  std::vector<Decoded> frames = decode_all(d, tolerance_pct);
  return frames.empty() ? Decoded() : frames.front();
}

Decoded decode_confirmed(const std::vector<uint32_t>& d, int min_count, int tolerance_pct) {
  std::vector<Decoded> frames = decode_all(d, tolerance_pct);
  for (size_t i = 0; i < frames.size(); ++i) {
    int seen = 0;
    for (const auto& other : frames) {
      if (other.code == frames[i].code && other.profile.bits == frames[i].profile.bits) ++seen;
    }
    if (seen >= min_count) return frames[i];
  }
  return Decoded();
}

std::string code_hex(uint32_t code, uint8_t bits) {
  char buf[16];
  int digits = (bits + 3) / 4;
  std::snprintf(buf, sizeof(buf), "%0*X", digits, code);
  return buf;
}

std::string code_fingerprint(uint32_t code, uint8_t bits) {
  return sha256_hex(std::to_string(bits) + ":" + code_hex(code, bits)).substr(0, 8);
}

}  // namespace gatecore
