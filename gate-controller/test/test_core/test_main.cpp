// Host tests for the firmware's pure logic: `pio test -e native`.
#include <unity.h>

#include <string>
#include <vector>

#include "crypto.h"
#include "guard.h"
#include "rf_frame.h"

using namespace gatecore;

void setUp() {}
void tearDown() {}

// ---------------------------------------------------------------- crypto

static void test_sha256_known_answers() {
  TEST_ASSERT_EQUAL_STRING("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                           sha256_hex("").c_str());
  TEST_ASSERT_EQUAL_STRING("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                           sha256_hex("abc").c_str());
  // Two blocks
  TEST_ASSERT_EQUAL_STRING("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
                           sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq").c_str());
}

static void test_hmac_rfc4231_case2() {
  TEST_ASSERT_EQUAL_STRING("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
                           hmac_sha256_hex("Jefe", "what do ya want for nothing?").c_str());
}

static void test_hmac_long_key_is_hashed() {
  // RFC 4231 case 6: a 131-byte key
  std::string key(131, char(0xaa));
  TEST_ASSERT_EQUAL_STRING("60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
                           hmac_sha256_hex(key, "Test Using Larger Than Block-Size Key - Hash Key First").c_str());
}

// The vector shared with lpr_app/utils/gate_signing.py and gate_agent/signing.py
static void test_contract_v2_shared_vector() {
  TEST_ASSERT_EQUAL_STRING("559348f51d1a09266f244fb2f15e33e2e8cf3376e0685785381bedd9e078241c",
                           sign("test-secret-0123456789", "POST", "/open", "1727500000000", "1727500000", "{}").c_str());
}

// ---------------------------------------------------------------- guard

static const char* SECRET = "device-secret";
static const int64_t NOW = 1727500000;

static Request signed_request(const std::string& path, uint64_t nonce, int64_t ts, const std::string& body = "{}") {
  Request r;
  r.method = path == "/status" ? "GET" : "POST";
  r.path = path;
  r.nonce = std::to_string(nonce);
  r.ts = std::to_string(ts);
  r.body = path == "/status" ? "" : body;
  r.signature = sign(SECRET, r.method, r.path, r.nonce, r.ts, r.body);
  return r;
}

static void test_valid_command_is_accepted_and_consumes_the_nonce() {
  Guard g;
  Request r = signed_request("/open", 100, NOW);
  TEST_ASSERT_EQUAL(200, g.authenticate(SECRET, r, true, NOW).status);
  TEST_ASSERT_EQUAL_UINT64(100, g.last_nonce());
  // Uppercase hex is the same signature
  Request upper = signed_request("/stop", 101, NOW);
  for (auto& ch : upper.signature) ch = char(toupper(ch));
  TEST_ASSERT_EQUAL(200, g.authenticate(SECRET, upper, true, NOW).status);
}

static void test_bad_signature_wrong_secret_or_tampering() {
  Guard g;
  Request r = signed_request("/open", 100, NOW);
  TEST_ASSERT_EQUAL(401, g.authenticate("other-secret", r, true, NOW).status);
  Request tampered = r;
  tampered.path = "/close";
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, tampered, true, NOW).status);
  tampered = r;
  tampered.body = "{\"x\":1}";
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, tampered, true, NOW).status);
  tampered = r;
  tampered.signature = "";
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, tampered, true, NOW).status);
  tampered = r;
  tampered.nonce = "12a";
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, tampered, true, NOW).status);
  TEST_ASSERT_EQUAL(401, g.authenticate("", r, true, NOW).status);
  // Nothing was consumed by any of it
  TEST_ASSERT_EQUAL_UINT64(0, g.last_nonce());
}

static void test_replayed_or_older_nonce_is_409() {
  Guard g;
  g.restore_nonce(500);  // as persisted before a reboot
  TEST_ASSERT_EQUAL(409, g.authenticate(SECRET, signed_request("/open", 500, NOW), true, NOW).status);
  TEST_ASSERT_EQUAL(409, g.authenticate(SECRET, signed_request("/open", 499, NOW), true, NOW).status);
  TEST_ASSERT_EQUAL(200, g.authenticate(SECRET, signed_request("/open", 501, NOW), true, NOW).status);
  TEST_ASSERT_EQUAL(409, g.authenticate(SECRET, signed_request("/open", 501, NOW), true, NOW).status);
}

static void test_timestamp_window_and_unsynced_clock() {
  Guard g;
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, signed_request("/open", 1, NOW - 31), true, NOW).status);
  TEST_ASSERT_EQUAL(401, g.authenticate(SECRET, signed_request("/open", 2, NOW + 31), true, NOW).status);
  TEST_ASSERT_EQUAL(200, g.authenticate(SECRET, signed_request("/open", 3, NOW - 30), true, NOW).status);
  Verdict v = g.authenticate(SECRET, signed_request("/open", 4, NOW), false, 0);
  TEST_ASSERT_EQUAL(503, v.status);
  TEST_ASSERT_EQUAL_STRING("clock_not_synced", v.error);
  // A fractional timestamp (as a v1 client sent) is read as whole seconds
  Request r = signed_request("/open", 5, NOW);
  r.ts = "1727500000.75";
  r.signature = sign(SECRET, r.method, r.path, r.nonce, r.ts, r.body);
  TEST_ASSERT_EQUAL(200, g.authenticate(SECRET, r, true, NOW).status);
}

static void test_interlock_rate_limit_and_stop_exempt() {
  Guard g;
  TEST_ASSERT_EQUAL(200, g.admit(Command::Open, 10000).status);
  TEST_ASSERT_EQUAL(409, g.admit(Command::Close, 10500).status);  // opposite within 1 s
  TEST_ASSERT_EQUAL(429, g.admit(Command::Open, 11000).status);   // same within 3 s
  TEST_ASSERT_EQUAL(429, g.admit(Command::Close, 12000).status);  // opposite after 1 s, before 3 s
  TEST_ASSERT_EQUAL(200, g.admit(Command::Stop, 10100).status);   // never limited
  TEST_ASSERT_EQUAL(200, g.admit(Command::Stop, 10101).status);
  TEST_ASSERT_EQUAL(200, g.admit(Command::Close, 13000).status);
  TEST_ASSERT_EQUAL(404, g.admit(Command::Unknown, 20000).status);
}

static void test_rate_limit_survives_millis_wraparound() {
  Guard g;
  TEST_ASSERT_EQUAL(200, g.admit(Command::Open, 0xFFFFF800u).status);
  TEST_ASSERT_EQUAL(429, g.admit(Command::Open, 0x00000100u).status);  // 0x900 = 2304 ms later
  TEST_ASSERT_EQUAL(200, g.admit(Command::Open, 0x00000C00u).status);  // 0x1400 = 5120 ms later
}

static void test_capture_commands_are_never_rate_limited() {
  Guard g;
  TEST_ASSERT_EQUAL(200, g.admit(Command::Open, 1000).status);
  TEST_ASSERT_EQUAL(200, g.admit(Command::Capture, 1001).status);
  TEST_ASSERT_EQUAL(200, g.admit(Command::SaveCode, 1002).status);
  // ...and do not count as motion either
  TEST_ASSERT_EQUAL(429, g.admit(Command::Open, 2000).status);
}

static void test_code_fingerprint() {
  TEST_ASSERT_EQUAL(8, code_fingerprint(0x12345A, 24).size());
  // Same value as lpr_app/services/barrier_simulator.code_fingerprint
  TEST_ASSERT_EQUAL_STRING("525403a6", code_fingerprint(0x12345A, 24).c_str());
  TEST_ASSERT_TRUE(code_fingerprint(0x12345A, 24) != code_fingerprint(0x12345B, 24));
  TEST_ASSERT_TRUE(code_fingerprint(0x12345A, 24) != code_fingerprint(0x12345A, 28));
  // Nothing of the code itself shows through
  TEST_ASSERT_TRUE(code_fingerprint(0x12345A, 24).find("345a") == std::string::npos);
}

static void test_parse_command() {
  TEST_ASSERT_TRUE(parse_command("/capture") == Command::Capture);
  TEST_ASSERT_TRUE(parse_command("/capture/save") == Command::SaveCode);
  TEST_ASSERT_TRUE(parse_command("/open") == Command::Open);
  TEST_ASSERT_TRUE(parse_command("/close") == Command::Close);
  TEST_ASSERT_TRUE(parse_command("/stop") == Command::Stop);
  TEST_ASSERT_TRUE(parse_command("/status") == Command::Status);
  TEST_ASSERT_TRUE(parse_command("/open/") == Command::Unknown);
}

// ---------------------------------------------------------------- rf frames

static void test_protocol1_frame_matches_rcswitch() {
  // RCSwitch protocol 1, 24 bits: code 0x000001 is 23 zeros, a one, then sync
  RfProfile p;
  std::vector<Pulse> f = encode_frame(0x000001, p);
  TEST_ASSERT_EQUAL(50, f.size());
  TEST_ASSERT_TRUE(f[0].high);
  TEST_ASSERT_EQUAL_UINT32(350, f[0].us);
  TEST_ASSERT_EQUAL_UINT32(1050, f[1].us);
  // Bit 24 (a one): HIGH 3T, LOW 1T
  TEST_ASSERT_EQUAL_UINT32(1050, f[46].us);
  TEST_ASSERT_EQUAL_UINT32(350, f[47].us);
  // Sync: HIGH 1T, LOW 31T
  TEST_ASSERT_TRUE(f[48].high);
  TEST_ASSERT_EQUAL_UINT32(350, f[48].us);
  TEST_ASSERT_FALSE(f[49].high);
  TEST_ASSERT_EQUAL_UINT32(10850, f[49].us);
  // Every bit is 4T, the sync 32T: 24*4 + 32 = 128 T
  TEST_ASSERT_EQUAL_UINT32(128 * 350, duration_us(f));
}

static void test_burst_repeats_frames_back_to_back() {
  RfProfile p;
  std::vector<Pulse> burst = encode_burst(0xABCDE1, p, 10);
  TEST_ASSERT_EQUAL_UINT32(10 * 128 * 350, duration_us(burst));
  // Levels alternate: nothing was merged by mistake
  for (size_t i = 1; i < burst.size(); ++i) TEST_ASSERT_TRUE(burst[i].high != burst[i - 1].high);
  TEST_ASSERT_EQUAL(0, encode_burst(1, p, 0).size());
}

static void test_ev1527_code_layout() {
  TEST_ASSERT_EQUAL_HEX32(0x12345A, ev1527_code(0x12345, 0xA));
  TEST_ASSERT_EQUAL_HEX32(0xFFFFF1, ev1527_code(0xFFFFFFF, 0x11));  // excess bits masked
  TEST_ASSERT_EQUAL_STRING("12345A", code_hex(0x12345A, 24).c_str());
}

static std::vector<uint32_t> as_durations(const std::vector<Pulse>& pulses, int jitter_pct = 0) {
  std::vector<uint32_t> d;
  int sign = 1;
  for (const auto& p : pulses) {
    d.push_back(p.us + uint32_t(int(p.us) * jitter_pct * sign / 100));
    sign = -sign;
  }
  return d;
}

static void test_decode_roundtrip_with_leading_noise_and_jitter() {
  RfProfile p;
  p.pulse_us = 320;  // a remote whose oscillator runs a little fast
  std::vector<Pulse> burst = encode_burst(0x5A5A5C, p, 3);
  std::vector<uint32_t> d = {120, 80, 500, 60};  // noise before the first sync
  for (auto v : as_durations(burst, 12)) d.push_back(v);
  Decoded got = decode(d);
  TEST_ASSERT_TRUE(got.ok);
  TEST_ASSERT_EQUAL_HEX32(0x5A5A5C, got.code);
  TEST_ASSERT_EQUAL(24, got.profile.bits);
  TEST_ASSERT_UINT32_WITHIN(40, 320, got.profile.pulse_us);
  TEST_ASSERT_EQUAL(3, got.profile.one_high);
  // Jitter moves the sync gap too; a receiver only needs it to be long
  TEST_ASSERT_UINT32_WITHIN(4, 31, got.profile.sync_low);
  // What we would transmit decodes back to the same code
  Decoded again = decode(as_durations(encode_burst(got.code, got.profile, 2)));
  TEST_ASSERT_TRUE(again.ok);
  TEST_ASSERT_EQUAL_HEX32(0x5A5A5C, again.code);
}

static void test_decode_rejects_incomplete_or_garbled_frames() {
  RfProfile p;
  std::vector<uint32_t> one_frame = as_durations(encode_frame(0x123456, p));
  // A frame needs a sync before and after it
  TEST_ASSERT_FALSE(decode(one_frame).ok);
  // Two frames: only the second lies between syncs, and one of its pulses is far off
  std::vector<uint32_t> d = as_durations(encode_burst(0x123456, p, 2));
  d[50 + 10] = d[50 + 10] * 2;
  TEST_ASSERT_FALSE(decode(d).ok);
  TEST_ASSERT_FALSE(decode({}).ok);
}

static void test_decode_skips_a_spoiled_frame() {
  RfProfile p;
  std::vector<uint32_t> d = as_durations(encode_burst(0x123456, p, 3));
  d[50 + 10] = d[50 + 10] * 2;  // the second frame is spoiled; the third is fine
  Decoded got = decode(d);
  TEST_ASSERT_TRUE(got.ok);
  TEST_ASSERT_EQUAL_HEX32(0x123456, got.code);
}

static void test_decode_confirmed_needs_two_identical_frames() {
  RfProfile p;
  // One frame between syncs: seen once, not confirmed
  TEST_ASSERT_FALSE(decode_confirmed(as_durations(encode_burst(0x0F0F0F, p, 2))).ok);
  Decoded got = decode_confirmed(as_durations(encode_burst(0x0F0F0F, p, 3)));
  TEST_ASSERT_TRUE(got.ok);
  TEST_ASSERT_EQUAL_HEX32(0x0F0F0F, got.code);
  TEST_ASSERT_EQUAL(2, decode_all(as_durations(encode_burst(0x0F0F0F, p, 3))).size());
  // Two different codes once each: nothing confirmed
  std::vector<Pulse> mixed = encode_burst(0x111111, p, 2);
  for (const auto& pulse : encode_frame(0x222222, p)) mixed.push_back(pulse);
  TEST_ASSERT_FALSE(decode_confirmed(as_durations(mixed)).ok);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_sha256_known_answers);
  RUN_TEST(test_hmac_rfc4231_case2);
  RUN_TEST(test_hmac_long_key_is_hashed);
  RUN_TEST(test_contract_v2_shared_vector);
  RUN_TEST(test_valid_command_is_accepted_and_consumes_the_nonce);
  RUN_TEST(test_bad_signature_wrong_secret_or_tampering);
  RUN_TEST(test_replayed_or_older_nonce_is_409);
  RUN_TEST(test_timestamp_window_and_unsynced_clock);
  RUN_TEST(test_interlock_rate_limit_and_stop_exempt);
  RUN_TEST(test_rate_limit_survives_millis_wraparound);
  RUN_TEST(test_parse_command);
  RUN_TEST(test_capture_commands_are_never_rate_limited);
  RUN_TEST(test_code_fingerprint);
  RUN_TEST(test_protocol1_frame_matches_rcswitch);
  RUN_TEST(test_burst_repeats_frames_back_to_back);
  RUN_TEST(test_ev1527_code_layout);
  RUN_TEST(test_decode_roundtrip_with_leading_noise_and_jitter);
  RUN_TEST(test_decode_rejects_incomplete_or_garbled_frames);
  RUN_TEST(test_decode_skips_a_spoiled_frame);
  RUN_TEST(test_decode_confirmed_needs_two_identical_frames);
  return UNITY_END();
}
