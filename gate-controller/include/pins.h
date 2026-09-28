// ESP32-S3-DevKitC-1 wiring (design.md §2). Change only this file for another board.
#pragma once

// CC1101 on the S3's FSPI pins. The CC1101 is a 3.3 V part: never feed it 5 V.
#define PIN_CC1101_SCK 12
#define PIN_CC1101_MOSI 11
#define PIN_CC1101_MISO 13
#define PIN_CC1101_CSN 10
#define PIN_CC1101_GDO0 4  // TX data in asynchronous OOK mode
#define PIN_CC1101_GDO2 5  // RX data, capture in setup mode only

// Optional arm feedback: the controller's UP/DOWN LIMIT OUTPUT dry contacts,
// each between the pin and ESP32 GND (internal pull-up; closed = LOW). Only
// with -DGATE_ARM_FEEDBACK=1, and only if the contacts are dry (task 0.4e).
#define PIN_LIMIT_UP 6
#define PIN_LIMIT_DOWN 7

// Held LOW at power-on for SETUP_HOLD_MS: start setup mode. The DevKit's BOOT button.
#define PIN_SETUP 0
#define SETUP_HOLD_MS 5000

// On-board RGB LED of the DevKitC-1 (WS2812 on GPIO48); used as a status light
#define PIN_STATUS_LED 48
