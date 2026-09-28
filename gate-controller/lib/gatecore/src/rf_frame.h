// Fixed-code 433 MHz remote frames (EV1527 / PT2262 family): encoding a code
// into the pulse train the radio sends, and decoding a captured pulse train
// back into a code. Timing is in microseconds.
//
// A frame is `bits` data bits, most significant first, then a sync:
//   bit 0 = HIGH zero_high*T, LOW zero_low*T
//   bit 1 = HIGH one_high*T,  LOW one_low*T
//   sync  = HIGH sync_high*T, LOW sync_low*T
// With T = 350 us, {1,3}, {3,1}, {1,31} this is RCSwitch's protocol 1, which
// EV1527 and PT2262 remotes use. A remote repeats the frame while a button is
// held, so a command sends it `repeats` times.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace gatecore {

struct Pulse {
  bool high;
  uint32_t us;
};

struct RfProfile {
  uint32_t pulse_us = 350;
  uint8_t zero_high = 1, zero_low = 3;
  uint8_t one_high = 3, one_low = 1;
  uint8_t sync_high = 1, sync_low = 31;
  uint8_t bits = 24;
};

// One frame: the data bits then the sync
std::vector<Pulse> encode_frame(uint32_t code, const RfProfile& profile);

// `repeats` frames back to back
std::vector<Pulse> encode_burst(uint32_t code, const RfProfile& profile, int repeats);

// Total duration of a pulse train
uint32_t duration_us(const std::vector<Pulse>& pulses);

// EV1527 code: a 20-bit address (the remote's identity) and 4 button bits
uint32_t ev1527_code(uint32_t address, uint8_t button_bits);

struct Decoded {
  bool ok = false;
  uint32_t code = 0;
  RfProfile profile;
};

// Decode the first complete frame in a capture. `durations` are the lengths of
// alternating levels starting with HIGH (as timed from edges on the receiver's
// data pin). Pulses within `tolerance_pct` of the expected length are
// accepted, since remotes' oscillators drift by a few percent.
Decoded decode(const std::vector<uint32_t>& durations, int tolerance_pct = 30);

// Every frame in a capture that decodes
std::vector<Decoded> decode_all(const std::vector<uint32_t>& durations, int tolerance_pct = 30);

// A code seen in at least `min_count` frames of a capture: a held button
// repeats its frame, while noise rarely decodes twice to the same code
Decoded decode_confirmed(const std::vector<uint32_t>& durations, int min_count = 2, int tolerance_pct = 30);

std::string code_hex(uint32_t code, uint8_t bits);

}  // namespace gatecore
