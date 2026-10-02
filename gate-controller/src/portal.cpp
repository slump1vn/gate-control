#include "portal.h"

#include <WiFi.h>
#include <esp_random.h>
#include <esp_task_wdt.h>

#include "config.h"
#include "device.h"
#include "net.h"
#include "radio.h"
#include "rf_frame.h"

namespace portal {

namespace {

// Setup mode ends after this long without anyone using the page
const uint32_t IDLE_TIMEOUT_MS = 10UL * 60 * 1000;
const uint32_t LISTEN_MS = 10000;
// Pairing: bursts sent while the receiver is in LEARN mode (about 3 s)
const int PAIR_BURSTS = 6;
// EV1527 button bits: a 4-button remote's A, B, C
const uint8_t PAIR_BUTTON_BITS[BUTTON_COUNT] = {0x8, 0x4, 0x2};
const char* BUTTON_LABEL[BUTTON_COUNT] = {"LÊN (mở)", "XUỐNG (đóng)", "DỪNG"};

WebServer* srv = nullptr;
bool setup_mode = true;
// "" in setup mode (page at /), "/ui" in normal mode (page at /ui/)
String base;
uint32_t last_use = 0;
// Shown once on the next page view, then cleared (keeps non-ASCII out of URLs)
String notice;

String esc(const String& s) {
  String out;
  out.reserve(s.length());
  for (char c : s) {
    switch (c) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      case '\'': out += "&#39;"; break;
      default: out += c;
    }
  }
  return out;
}

String url(const char* path) { return base + path; }

// Normal mode: the page can open the gate, so it needs the setup password.
// Setup mode: the access point's WPA2 password already guards it.
bool allowed() {
  last_use = millis();
  if (setup_mode) return true;
  if (srv->authenticate("admin", device::cfg.setup_password.c_str())) return true;
  srv->requestAuthentication(BASIC_AUTH, "gate-ctl", "Đăng nhập: admin / mật khẩu cài đặt trên nhãn thiết bị");
  return false;
}

bool parse_button(Button* out) {
  String b = srv->arg("button");
  for (int i = 0; i < BUTTON_COUNT; ++i) {
    if (b == button_name(Button(i))) {
      *out = Button(i);
      return true;
    }
  }
  return false;
}

void back(const String& msg) {
  notice = msg;
  srv->sendHeader("Location", url("/"));
  srv->send(303);
}

String form_button(const char* action, const String& label, const String& button = "", const char* confirm = nullptr) {
  String f = "<form method=post action=" + url(action);
  if (confirm) f += " onsubmit=\"return confirm('" + String(confirm) + "')\"";
  f += ">";
  if (button.length()) f += "<input type=hidden name=button value=" + button + ">";
  return f + "<button>" + label + "</button></form> ";
}

String row(const char* label, const String& value, bool good = true) {
  return String("<tr><td>") + label + "</td><td" + (good ? "" : " class=bad") + ">" + value + "</td></tr>";
}

String fingerprint(const ButtonCode& code) {
  if (!code.set) return "&mdash;";
  return "<code>" + String(gatecore::code_fingerprint(code.code, code.profile.bits).c_str()) + "</code> (" +
         String(code.profile.bits) + " bit, " + String(code.profile.pulse_us) + " µs)";
}

String diagnostics() {
  const Config& c = device::cfg;
  radio::Diag r = radio::diag();
  bool chip_ok = r.found && r.partnum == 0x00 && (r.version == 0x14 || r.version == 0x04);
  String h = F("<h2>Kiểm tra kết nối</h2><table>");
  h += row("Firmware", GATE_FW_VERSION);
  h += row("Chế độ", setup_mode ? "cài đặt (WiFi riêng của thiết bị)" : "hoạt động");
  h += row("Đã chạy", String(device::uptime_s() / 60) + " phút · RAM trống " + String(ESP.getFreeHeap() / 1024) + " KB");
  if (!r.found) {
    h += row("Module CC1101", "KHÔNG trả lời qua SPI: kiểm tra dây SCK/MOSI/MISO/CSN và nguồn 3V3", false);
  } else {
    char id[48];
    snprintf(id, sizeof(id), "PARTNUM 0x%02X, VERSION 0x%02X", r.partnum, r.version);
    h += row("Module CC1101", String(chip_ok ? "OK · " : "trả lời lạ · ") + id, chip_ok);
    h += row("Tần số / công suất", String(r.frequency_mhz, 2) + " MHz · " + String(r.power_dbm) + " dBm");
  }
  if (setup_mode) {
    h += row("WiFi", "tắt trong chế độ cài đặt · chính: " + esc(c.wifi_ssid) +
                         (c.wifi2_ssid.length() ? " · dự phòng: " + esc(c.wifi2_ssid) : String()));
  } else if (net::connected()) {
    int rssi = net::rssi();
    h += row("WiFi", String(net::on_backup() ? "dự phòng" : "chính") + " \"" + esc(net::active_ssid()) + "\" · IP " +
                         net::ip() + " · " + String(rssi) + " dBm", rssi > -75);
  } else {
    h += row("WiFi", "chưa kết nối (đang thử " + String(net::on_backup() ? "mạng dự phòng" : "mạng chính") + ")", false);
  }
  if (!setup_mode) {
    int hb = net::last_heartbeat_status();
    String hb_text = hb == 0   ? String("chưa gửi")
                     : hb < 0  ? "không tới được server (lỗi " + String(hb) + ")"
                               : "HTTP " + String(hb) + (hb == 200 ? " · OK" : "");
    if (hb != 0) hb_text += " · " + String(net::last_heartbeat_age_s()) + " giây trước";
    h += row("Server (heartbeat)", hb_text, hb == 200);
    h += row("Đồng hồ", net::clock_synced() ? "đã đồng bộ" : "chưa đồng bộ: mọi lệnh bị từ chối", net::clock_synced());
  }
  h += row("Chạy thử (dry run)", c.dry_run ? "BẬT: lệnh từ hệ thống không phát sóng" : "tắt", !c.dry_run);
  h += row("Lệnh gần nhất", esc(device::last_result().length() ? device::last_result() : String("—")));
  return h + "</table><form method=get action=" + url("/") + "><button>Làm mới</button></form>";
}

void page() {
  if (!allowed()) return;
  const Config& c = device::cfg;
  String html;
  html.reserve(9000);
  html += F("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Bộ điều khiển cổng</title><style>body{font:15px sans-serif;max-width:760px;margin:auto;padding:12px}"
            "input{width:100%;padding:6px;margin:2px 0 8px;box-sizing:border-box}td{padding:4px 6px;vertical-align:top}"
            "form{display:inline}button{padding:6px 10px;margin:2px 0}.msg{background:#fff3cd;padding:8px}"
            ".bad{color:#b00020}h2{margin-top:24px;border-bottom:1px solid #ddd}</style>");
  html += "<h1>Bộ điều khiển cổng · gate " + String(c.gate_id) + "</h1>";
  if (setup_mode) html += F("<p>Chế độ cài đặt tự thoát sau 10 phút không dùng.</p>");
  if (notice.length()) {
    html += "<p class=msg>" + esc(notice) + "</p>";
    notice = "";
  }
  html += diagnostics();

  html += F("<h2>Đọc tín hiệu remote</h2><p>Bấm <b>Nghe</b> rồi giữ một nút của remote gần thiết bị trong 10 giây. "
            "Thiết bị cho biết có bắt được sóng không, mạnh cỡ nào và giải mã được gì. Chỉ đọc, không lưu gì.</p>");
  html += form_button("/listen", "Nghe 10 giây");

  html += F("<h2>Thử điều khiển cổng</h2><p>Phát mã đã lưu <b>ngay</b>, kể cả khi đang chạy thử. "
            "Vẫn chịu khoá liên động LÊN/XUỐNG và giới hạn 1 lệnh/3 giây.</p>");
  html += form_button("/test", "Mở cổng", "up", "Phát lệnh MỞ cổng thật?");
  html += form_button("/test", "Đóng cổng", "down", "Phát lệnh ĐÓNG cổng thật? Kiểm tra không có xe dưới thanh chắn.");
  html += form_button("/test", "Dừng", "stop");

  html += F("<h2>Mã remote đã lưu</h2><p><b>Ghép</b>: bật chế độ LEARN trên bộ thu của barie trước, thiết bị sẽ "
            "phát mã riêng của nó. <b>Chép</b>: giữ nút tương ứng của remote gần thiết bị trong 10 giây. "
            "Chỉ hiện dấu vân tay của mã.</p><table>");
  for (int b = 0; b < BUTTON_COUNT; ++b) {
    String name = button_name(Button(b));
    html += "<tr><td>" + String(BUTTON_LABEL[b]) + "</td><td>" + fingerprint(c.buttons[b]) + "</td><td>" +
            form_button("/pair", "Ghép", name) + form_button("/capture", "Chép", name) + "</td></tr>";
  }
  html += F("</table>");

  html += "<h2>Mạng và cổng</h2><form method=post action=" + url("/save") + ">";
  html += "WiFi chính<input name=ssid value=\"" + esc(c.wifi_ssid) + "\">";
  html += "Mật khẩu WiFi chính (để trống: giữ nguyên)<input name=wifipw type=password>";
  html += "WiFi dự phòng (dùng khi mạng chính không vào được 20 giây)<input name=ssid2 value=\"" + esc(c.wifi2_ssid) + "\">";
  html += "Mật khẩu WiFi dự phòng (để trống: giữ nguyên)<input name=wifipw2 type=password>";
  html += F("<label><input type=checkbox name=clear2 value=1 style='width:auto'> Xoá WiFi dự phòng</label><br>");
  html += "IP tĩnh trên WiFi chính (để trống: DHCP; WiFi dự phòng luôn dùng DHCP)<input name=ip placeholder=172.87.80.254 value=\"" +
          esc(c.static_ip) + "\">";
  html += "Subnet mask<input name=mask placeholder=255.255.252.0 value=\"" + esc(c.static_mask) + "\">";
  html += "Gateway<input name=gw placeholder=172.87.80.1 value=\"" + esc(c.static_gw) + "\">";
  html += "DNS (để trống: dùng gateway)<input name=dns value=\"" + esc(c.static_dns) + "\">";
  html += "Gate id (trong /manage/gates)<input name=gate value=\"" + String(c.gate_id) + "\">";
  html += String("Token bộ điều khiển (để trống: giữ nguyên") + (c.secret.length() ? ", đã có" : ", CHƯA CÓ") +
          ")<input name=secret type=password>";
  html += "Địa chỉ heartbeat<input name=hburl placeholder=http://192.168.2.80:8000/api/v1/gate/heartbeat/ value=\"" +
          esc(c.heartbeat_url) + "\">";
  html += "Máy chủ NTP<input name=ntp value=\"" + esc(c.ntp_server) + "\">";
  html += "Tần số (MHz)<input name=freq value=\"" + String(c.frequency_mhz, 3) + "\">";
  html += "Số khung mỗi lệnh<input name=repeats value=\"" + String(c.repeats) + "\">";
  html += F("Mật khẩu cài đặt mới: WiFi riêng của thiết bị và đăng nhập trang này, 8-63 ký tự "
            "(để trống: giữ nguyên)<input name=setuppw type=password minlength=8 maxlength=63>");
  html += F("<button>Lưu</button></form><p>Mạng và token có hiệu lực sau khi khởi động lại.</p>");

  html += "<h2>Chạy thử: " + String(c.dry_run ? "BẬT" : "tắt") + "</h2>";
  html += "<form method=post action=" + url("/dryrun") + "><input type=hidden name=on value=" +
          String(c.dry_run ? "0" : "1") + "><button>" + String(c.dry_run ? "Tắt chạy thử" : "Bật chạy thử") +
          "</button></form>";
  html += F("<h2>Khởi động lại</h2>");
  html += form_button("/restart", setup_mode ? "Lưu và vào chế độ hoạt động" : "Khởi động lại");
  srv->send(200, "text/html; charset=utf-8", html);
}

void save() {
  if (!allowed()) return;
  Config& c = device::cfg;
  if (srv->hasArg("ssid")) c.wifi_ssid = srv->arg("ssid");
  if (srv->arg("wifipw").length()) c.wifi_password = srv->arg("wifipw");
  if (srv->arg("clear2") == "1") {
    c.wifi2_ssid = "";
    c.wifi2_password = "";
  } else {
    if (srv->hasArg("ssid2")) c.wifi2_ssid = srv->arg("ssid2");
    if (srv->arg("wifipw2").length()) c.wifi2_password = srv->arg("wifipw2");
  }
  if (srv->hasArg("ip")) {
    String ip = srv->arg("ip"), mask = srv->arg("mask"), gw = srv->arg("gw"), dns = srv->arg("dns");
    ip.trim(); mask.trim(); gw.trim(); dns.trim();
    IPAddress check;
    if (ip.length() && (!check.fromString(ip) || !check.fromString(mask) || !check.fromString(gw) ||
                        (dns.length() && !check.fromString(dns)))) {
      return back("IP tĩnh cần địa chỉ, subnet mask và gateway hợp lệ; chưa lưu gì.");
    }
    c.static_ip = ip;
    c.static_mask = ip.length() ? mask : "";
    c.static_gw = ip.length() ? gw : "";
    c.static_dns = ip.length() ? dns : "";
  }
  if (srv->hasArg("gate")) c.gate_id = strtoul(srv->arg("gate").c_str(), nullptr, 10);
  if (srv->arg("secret").length()) c.secret = srv->arg("secret");
  if (srv->hasArg("hburl")) c.heartbeat_url = srv->arg("hburl");
  if (srv->arg("ntp").length()) c.ntp_server = srv->arg("ntp");
  float freq = srv->arg("freq").toFloat();
  if (freq >= 433.05f && freq <= 434.79f) c.frequency_mhz = freq;
  long repeats = srv->arg("repeats").toInt();
  if (repeats >= 1 && repeats <= 30) c.repeats = uint8_t(repeats);
  String setuppw = srv->arg("setuppw");
  if (setuppw.length() && (setuppw.length() < 8 || setuppw.length() > 63)) {
    return back("Mật khẩu cài đặt phải dài 8 đến 63 ký tự; chưa lưu gì.");
  }
  if (setuppw.length()) c.setup_password = setuppw;
  config_store::save(c);
  back(setuppw.length() ? "Đã lưu, kể cả mật khẩu cài đặt mới (đăng nhập lại bằng mật khẩu mới)." : c.provisioned() ? "Đã lưu. Khởi động lại để dùng cấu hình mạng mới."
                       : "Đã lưu, nhưng còn thiếu WiFi, gate id, token hoặc địa chỉ heartbeat.");
}

void listen() {
  if (!allowed()) return;
  esp_task_wdt_reset();
  std::vector<uint32_t> edges = radio::capture(LISTEN_MS);
  esp_task_wdt_reset();
  int rssi = radio::last_capture_rssi();
  std::vector<gatecore::Decoded> frames = gatecore::decode_all(edges);
  gatecore::Decoded got = gatecore::decode_confirmed(edges);
  String msg = "Nghe 10 giây: " + String(edges.size()) + " sườn xung, " + String(frames.size()) +
               " khung giải mã được, sóng mạnh nhất " + (rssi ? String(rssi) + " dBm" : String("không đo được")) + ". ";
  if (got.ok) {
    msg += "Mã cố định (dấu vân tay " + String(gatecore::code_fingerprint(got.code, got.profile.bits).c_str()) + ", " +
           String(got.profile.bits) + " bit, xung " + String(got.profile.pulse_us) + " µs): sao chép được.";
  } else if (!frames.empty()) {
    msg += "Có khung giải mã được nhưng không lặp lại giống nhau: có thể là mã nhảy (rolling code), không sao chép được.";
  } else if (edges.size() > 50) {
    msg += "Có sóng nhưng không giải mã được: remote có thể dùng mã khác loại EV1527/PT2262, hoặc tần số khác 433,92 MHz.";
  } else {
    msg += "Không bắt được tín hiệu: giữ remote gần hơn và giữ nút suốt 10 giây.";
  }
  back(msg);
}

void pair() {
  if (!allowed()) return;
  Button b;
  if (!parse_button(&b)) return back("Nút không hợp lệ");
  Config& c = device::cfg;
  if (!c.ev1527_address) {
    // This device's own remote identity, generated once
    do c.ev1527_address = esp_random() & 0xFFFFF; while (!c.ev1527_address);
  }
  ButtonCode& code = c.buttons[b];
  device::lock();
  code.set = true;
  code.code = gatecore::ev1527_code(c.ev1527_address, PAIR_BUTTON_BITS[b]);
  code.profile = gatecore::RfProfile();
  device::unlock();
  config_store::save(c);
  for (int i = 0; i < PAIR_BURSTS; ++i) {
    esp_task_wdt_reset();
    device::transmit_button(b);
    delay(100);
  }
  back(String("Đã phát mã ghép cho nút ") + BUTTON_LABEL[b] + " (dấu vân tay " +
       gatecore::code_fingerprint(code.code, 24).c_str() + "). Nếu bộ thu đã học, thử bằng nút điều khiển.");
}

void capture() {
  if (!allowed()) return;
  Button b;
  if (!parse_button(&b)) return back("Nút không hợp lệ");
  esp_task_wdt_reset();
  std::vector<uint32_t> edges = radio::capture(LISTEN_MS);
  esp_task_wdt_reset();
  gatecore::Decoded got = gatecore::decode_confirmed(edges);
  if (!got.ok) {
    return back(String("Không giải mã được mã nào hai lần (") + edges.size() +
                " sườn xung). Giữ remote gần hơn, giữ nút suốt 10 giây.");
  }
  device::lock();
  ButtonCode& code = device::cfg.buttons[b];
  code.set = true;
  code.code = got.code;
  code.profile = got.profile;
  device::unlock();
  config_store::save(device::cfg);
  back(String("Đã chép nút ") + BUTTON_LABEL[b] + ": dấu vân tay " +
       gatecore::code_fingerprint(got.code, got.profile.bits).c_str());
}

void test() {
  if (!allowed()) return;
  Button b;
  if (!parse_button(&b)) return back("Nút không hợp lệ");
  if (!device::cfg.buttons[b].set) return back(String("Chưa có mã cho nút ") + BUTTON_LABEL[b] + ": ghép hoặc chép trước.");
  gatecore::Command cmd = b == BUTTON_UP ? gatecore::Command::Open
                        : b == BUTTON_DOWN ? gatecore::Command::Close
                                           : gatecore::Command::Stop;
  // The same interlock and rate limit as commands from the system
  gatecore::Verdict v = device::guard.admit(cmd, millis());
  if (!v.ok()) return back(String("Bị chặn (") + v.error + "): chờ vài giây rồi thử lại.");
  bool sent = device::transmit_button(b);
  device::set_last_result(String("test ") + gatecore::command_name(cmd) + (sent ? ": sent" : ": tx_failed"));
  back(sent ? String("Đã phát lệnh ") + BUTTON_LABEL[b] + "." : String("Phát sóng thất bại: kiểm tra module CC1101."));
}

void dry_run() {
  if (!allowed()) return;
  device::cfg.dry_run = srv->arg("on") == "1";
  config_store::save(device::cfg);
  back(device::cfg.dry_run ? "Đã bật chạy thử." : "Đã tắt chạy thử: lệnh từ hệ thống sẽ điều khiển barie thật.");
}

void restart() {
  if (!allowed()) return;
  srv->send(200, "text/plain; charset=utf-8", "Đang khởi động lại…");
  delay(500);
  ESP.restart();
}

void refuse_commands() {
  srv->send(503, "application/json", "{\"ok\":false,\"error\":\"setup_mode\"}");
}

void add_routes(WebServer& server) {
  server.on(url("/"), HTTP_GET, page);
  server.on(url("/save"), HTTP_POST, save);
  server.on(url("/listen"), HTTP_POST, listen);
  server.on(url("/pair"), HTTP_POST, pair);
  server.on(url("/capture"), HTTP_POST, capture);
  server.on(url("/test"), HTTP_POST, test);
  server.on(url("/dryrun"), HTTP_POST, dry_run);
  server.on(url("/restart"), HTTP_POST, restart);
}

void ensure_password() {
  Config& c = device::cfg;
  if (c.setup_password.length() >= 8) return;
  // First boot: make a password and show it on the USB console, for the device label
  const char* alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  c.setup_password = "";
  for (int i = 0; i < 12; ++i) c.setup_password += alphabet[esp_random() % 31];
  config_store::save(c);
}

}  // namespace

String begin(WebServer& server) {
  srv = &server;
  setup_mode = true;
  base = "";
  ensure_password();
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char name[20];
  snprintf(name, sizeof(name), "gate-ctl-%02x%02x", mac[4], mac[5]);
  WiFi.mode(WIFI_AP);
  WiFi.softAP(name, device::cfg.setup_password.c_str());
  Serial.printf("Setup mode: WiFi \"%s\", password \"%s\", open http://%s/\n", name,
                device::cfg.setup_password.c_str(), WiFi.softAPIP().toString().c_str());
  add_routes(server);
  server.onNotFound(refuse_commands);
  server.begin();
  last_use = millis();
  return name;
}

void begin_status(WebServer& server) {
  srv = &server;
  setup_mode = false;
  base = "/ui";
  ensure_password();
  add_routes(server);
  // /ui without the slash
  server.on("/ui", HTTP_GET, []() {
    srv->sendHeader("Location", "/ui/");
    srv->send(302);
  });
}

void tick() {
  if (setup_mode && millis() - last_use > IDLE_TIMEOUT_MS) {
    Serial.println("Setup mode unused for 10 minutes: restarting");
    ESP.restart();
  }
}

}  // namespace portal
