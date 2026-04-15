/*
 * Smart Gym Rep Counter - ESP32 Firmware
 *
 * Hardware:
 *   ESP32 Dev Board + ADXL345 accelerometer
 *   I2C wiring: SDA -> GPIO21, SCL -> GPIO22, VCC -> 3V3, GND -> GND
 *   ADXL345 I2C address: 0x53 (SDO -> GND) or 0x1D (SDO -> VCC)
 *
 * Behavior:
 *   - Samples accelerometer at 50 Hz
 *   - Maintains a 50-sample sliding window with a 25-sample stride
 *   - POSTs each completed window as JSON to /api/ingest on the backend
 */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>

// -------- USER CONFIG --------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASS       "YOUR_WIFI_PASSWORD"
#define SERVER_URL      "http://192.168.1.10:8000/api/ingest"
#define AUTH_TOKEN      "REPLACE_WITH_JWT_TOKEN"
#define DEVICE_ID       "gym-esp32-01"
// -----------------------------

// ADXL345
static const uint8_t ADXL345_ADDR       = 0x53;
static const uint8_t ADXL345_REG_POWER  = 0x2D;
static const uint8_t ADXL345_REG_DATAFMT= 0x31;
static const uint8_t ADXL345_REG_BWRATE = 0x2C;
static const uint8_t ADXL345_REG_DATAX0 = 0x32;

static const int SDA_PIN = 21;
static const int SCL_PIN = 22;

static const int WINDOW_SIZE  = 50;   // 1s at 50Hz
static const int WINDOW_STRIDE= 25;   // 0.5s stride
static const int SAMPLE_HZ    = 50;
static const unsigned long SAMPLE_PERIOD_MS = 1000UL / SAMPLE_HZ;

// Ring buffers for ax, ay, az (float g's)
float bufAx[WINDOW_SIZE];
float bufAy[WINDOW_SIZE];
float bufAz[WINDOW_SIZE];
int   writeIdx   = 0;           // next write position (0..WINDOW_SIZE-1)
int   filled     = 0;           // how many valid samples ever written (capped at WINDOW_SIZE)
int   sinceEmit  = 0;           // samples since last emission

unsigned long lastSampleMs = 0;

// -------- ADXL345 helpers --------
void adxlWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(ADXL345_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool adxlReadXYZ(int16_t &x, int16_t &y, int16_t &z) {
  Wire.beginTransmission(ADXL345_ADDR);
  Wire.write(ADXL345_REG_DATAX0);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)ADXL345_ADDR, 6) != 6) return false;
  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.read();
  x = (int16_t)((b[1] << 8) | b[0]);
  y = (int16_t)((b[3] << 8) | b[2]);
  z = (int16_t)((b[5] << 8) | b[4]);
  return true;
}

void adxlInit() {
  // Full resolution, +/- 4g range (range bits = 01)
  adxlWrite(ADXL345_REG_DATAFMT, 0x09);
  // Output data rate = 100Hz (we throttle to 50 in loop), normal power
  adxlWrite(ADXL345_REG_BWRATE, 0x0A);
  // Measure mode
  adxlWrite(ADXL345_REG_POWER,  0x08);
  delay(20);
}

// -------- WiFi --------
void wifiConnect() {
  Serial.printf("Connecting to WiFi '%s'...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000UL) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK, IP=");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi FAILED (continuing, will retry in loop)");
  }
}

// -------- JSON builder --------
// Build payload in-place to avoid large String concatenations.
String buildPayload() {
  // Re-order ring buffer to start at oldest sample.
  // Oldest sample index = writeIdx (wraps) since ring is full.
  String p;
  p.reserve(4096);
  p += "{\"device_id\":\"";
  p += DEVICE_ID;
  p += "\",\"timestamp\":";
  p += String((uint32_t)(millis() / 1000));
  p += ",\"ax\":[";
  for (int i = 0; i < WINDOW_SIZE; i++) {
    int idx = (writeIdx + i) % WINDOW_SIZE;
    p += String(bufAx[idx], 4);
    if (i < WINDOW_SIZE - 1) p += ",";
  }
  p += "],\"ay\":[";
  for (int i = 0; i < WINDOW_SIZE; i++) {
    int idx = (writeIdx + i) % WINDOW_SIZE;
    p += String(bufAy[idx], 4);
    if (i < WINDOW_SIZE - 1) p += ",";
  }
  p += "],\"az\":[";
  for (int i = 0; i < WINDOW_SIZE; i++) {
    int idx = (writeIdx + i) % WINDOW_SIZE;
    p += String(bufAz[idx], 4);
    if (i < WINDOW_SIZE - 1) p += ",";
  }
  p += "]}";
  return p;
}

void sendWindow() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("sendWindow: WiFi down, skipping");
    return;
  }
  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  String payload = buildPayload();
  int code = http.POST(payload);
  if (code > 0) {
    Serial.printf("POST %d\n", code);
    if (code == 200) {
      String body = http.getString();
      Serial.println(body);
    }
  } else {
    Serial.printf("POST failed: %s\n", http.errorToString(code).c_str());
  }
  http.end();
}

// -------- Arduino lifecycle --------
void setup() {
  Serial.begin(115200);
  delay(100);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  adxlInit();
  wifiConnect();
  lastSampleMs = millis();
  Serial.println("Ready.");
}

void loop() {
  // Attempt to reconnect WiFi silently if disconnected
  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastRetry = 0;
    if (millis() - lastRetry > 5000) {
      lastRetry = millis();
      WiFi.reconnect();
    }
  }

  unsigned long now = millis();
  if (now - lastSampleMs < SAMPLE_PERIOD_MS) return;
  lastSampleMs += SAMPLE_PERIOD_MS;

  int16_t rx, ry, rz;
  if (!adxlReadXYZ(rx, ry, rz)) {
    Serial.println("ADXL345 read failed");
    return;
  }
  // Full-res +-4g => 256 LSB/g (actually 1 LSB = 3.9mg in full-res across all ranges)
  const float LSB_PER_G = 256.0f;
  float ax = rx / LSB_PER_G;
  float ay = ry / LSB_PER_G;
  float az = rz / LSB_PER_G;

  bufAx[writeIdx] = ax;
  bufAy[writeIdx] = ay;
  bufAz[writeIdx] = az;
  writeIdx = (writeIdx + 1) % WINDOW_SIZE;
  if (filled < WINDOW_SIZE) filled++;
  sinceEmit++;

  if (filled >= WINDOW_SIZE && sinceEmit >= WINDOW_STRIDE) {
    sinceEmit = 0;
    sendWindow();
  }
}
