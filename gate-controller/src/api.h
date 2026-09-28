// The controller contract v2 served in normal mode: POST /open /close /stop,
// GET /status, every request signed (see lib/gatecore/src/guard.h).
#pragma once

#include <WebServer.h>

namespace api {

void begin(WebServer& server);

}  // namespace api
