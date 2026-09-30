// Board wiring (design.md §2, README.md). Change only this file for another board.
//   default                 ESP32-S3-DevKitC-1
//   -DGATE_BOARD_ESP32DEV   ESP-WROOM-32 DevKit (30-pin, USB-C/CH340C)
#pragma once

#if defined(GATE_BOARD_ESP32DEV)

// CC1101 on the ESP32's VSPI pins, all on the bottom header row. The CC1101 is
// a 3.3 V part: 3V3 pin, never VIN/5 V.
#define PIN_CC1101_SCK 18
#define PIN_CC1101_MOSI 23
#define PIN_CC1101_MISO 19
#define PIN_CC1101_CSN 5    // a strapping pin, but the CC1101 does not pull it
#define PIN_CC1101_GDO0 4   // TX data in asynchronous OOK mode
#define PIN_CC1101_GDO2 16  // RX data, capture only (not GPIO2/12: they decide how the chip boots)

// Optional arm feedback, dry contacts to GND (internal pull-ups; GPIO34-39 have none)
#define PIN_LIMIT_UP 32
#define PIN_LIMIT_DOWN 33

// BOOT button
#define PIN_SETUP 0
#define SETUP_HOLD_MS 5000

// The blue "D2" LED on the board: one colour, so states are blink patterns
#define PIN_STATUS_LED 2
#define STATUS_LED_RGB 0

#else  // ESP32-S3-DevKitC-1

// CC1101 on the S3's FSPI pins. The CC1101 is a 3.3 V part: never feed it 5 V.
#define PIN_CC1101_SCK 12
#define PIN_CC1101_MOSI 11
#define PIN_CC1101_MISO 13
#define PIN_CC1101_CSN 10
#define PIN_CC1101_GDO0 4  // TX data in asynchronous OOK mode
#define PIN_CC1101_GDO2 5  // RX data, capture only

// Optional arm feedback: the controller's UP/DOWN LIMIT OUTPUT dry contacts,
// each between the pin and ESP32 GND (internal pull-up; closed = LOW). Only
// with -DGATE_ARM_FEEDBACK=1, and only if the contacts are dry (task 0.4e).
#define PIN_LIMIT_UP 6
#define PIN_LIMIT_DOWN 7

// Held LOW at power-on for SETUP_HOLD_MS: start setup mode. The DevKit's BOOT button.
#define PIN_SETUP 0
#define SETUP_HOLD_MS 5000

// On-board RGB LED of the DevKitC-1 (WS2812 on GPIO48)
#define PIN_STATUS_LED 48
#define STATUS_LED_RGB 1

#endif
