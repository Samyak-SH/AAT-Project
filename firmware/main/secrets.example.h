/*
 * secrets.example.h — copy this file to `secrets.h` (same directory) and fill
 * in your own values. `secrets.h` is gitignored so your credentials never land
 * in source control.
 *
 *   cp secrets.example.h secrets.h
 *   # then edit secrets.h
 */
#pragma once

#define WIFI_SSID   "YourWiFiName"
#define WIFI_PASS   "YourWiFiPassword"

// Backend: use your Mac/Linux host's LAN IP (NOT localhost).
// Port 8001 matches the `8001:8000` mapping in docker-compose.yml.
//   macOS: ipconfig getifaddr en0
//   Linux: ip route get 1 | awk '{print $7; exit}'
#define SERVER_URL  "http://192.168.1.XX:8001/api/ingest"

// Anything unique — used by the backend for rate-limiting and logs.
#define DEVICE_ID   "esp32-01"
