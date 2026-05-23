#include <Wire.h>
#include <math.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "MAX30105.h"

#define SDA_PIN 6   // XIAO ESP32C3 D4
#define SCL_PIN 7   // XIAO ESP32C3 D5

// ===============================
// WiFi + Backend Config
// ===============================
const char* WIFI_SSID = "CMCC-3e6e-5G";
const char* WIFI_PASSWORD = "b6efrff7";

// 后端队友电脑 IP
const char* SERVER_URL = "http://172.20.10.2:8000/api/heartbeat";
const char* COMMAND_URL = "http://172.20.10.2:8000/api/device-command";

// 数据上传间隔：10 秒发一次有效数据
unsigned long lastPostTime = 0;
const unsigned long POST_INTERVAL = 10000;

// 命令轮询：每 1 秒问一次后端有没有 start / stop
unsigned long lastCommandPollTime = 0;
const unsigned long COMMAND_POLL_INTERVAL = 1000;

// 测试状态
bool testActive = false;
unsigned long testStartTime = 0;
const unsigned long TEST_TIMEOUT = 60000;  // 单次测试最多 60 秒

MAX30105 sensor;

// ===============================
// CycleSense Final Demo Parameters
// ===============================
const long FINGER_THRESHOLD = 30000;
const byte RED_LED = 0x10;
const byte IR_LED  = 0x18;

// 静息心率范围过滤：50–120 BPM
const long MIN_VALID_IBI = 500;   // 120 BPM
const long MAX_VALID_IBI = 1200;  // 50 BPM

// IBI buffer
const int IBI_SIZE = 12;
long ibiBuffer[IBI_SIZE];
int ibiCount = 0;
int ibiIndex = 0;

// Finger / timing state
bool fingerWasPresent = false;
unsigned long fingerStartTime = 0;
unsigned long lastStatusPrint = 0;
unsigned long lastQuickOutput = 0;

// Beat detection state
unsigned long lastBeatTime = 0;
unsigned long lastPeakTime = 0;

// Custom signal processing
float baseline = 0;
float envelope = 0;
float prevAc = 0;
bool wasRising = false;

// Signal quality window
long sigMin = 999999;
long sigMax = 0;
int sigCount = 0;

// Last valid display values
float lastValidBpm = 0;

// Function declarations
void connectWiFi();
void pollBackendCommand();
void postToBackend(float heartRate, float hrvRmssd);
void resetMeasurement();

// ===============================
// Utility functions
// ===============================
void resetSignalWindow() {
  sigMin = 999999;
  sigMax = 0;
  sigCount = 0;
}

void updateSignalWindow(long irValue) {
  if (irValue < sigMin) sigMin = irValue;
  if (irValue > sigMax) sigMax = irValue;
  sigCount++;
}

void resetMeasurement() {
  ibiCount = 0;
  ibiIndex = 0;

  lastBeatTime = 0;
  lastPeakTime = 0;
  lastValidBpm = 0;

  baseline = 0;
  envelope = 0;
  prevAc = 0;
  wasRising = false;

  for (int i = 0; i < IBI_SIZE; i++) {
    ibiBuffer[i] = 0;
  }

  resetSignalWindow();
}

void addIBI(long ibi) {
  ibiBuffer[ibiIndex] = ibi;
  ibiIndex = (ibiIndex + 1) % IBI_SIZE;

  if (ibiCount < IBI_SIZE) {
    ibiCount++;
  }
}

float averageIBI() {
  if (ibiCount == 0) return 0;

  long sum = 0;
  for (int i = 0; i < ibiCount; i++) {
    sum += ibiBuffer[i];
  }

  return (float)sum / ibiCount;
}

float calculateBPM() {
  float avgIbi = averageIBI();
  if (avgIbi <= 0) return 0;
  return 60000.0 / avgIbi;
}

float calculateRMSSD() {
  if (ibiCount < 4) return 0;

  float sumSq = 0;
  int n = 0;

  for (int i = 1; i < ibiCount; i++) {
    int prevIndex = (ibiIndex - ibiCount + i - 1 + IBI_SIZE) % IBI_SIZE;
    int currIndex = (ibiIndex - ibiCount + i + IBI_SIZE) % IBI_SIZE;

    long diff = ibiBuffer[currIndex] - ibiBuffer[prevIndex];

    // 防止假峰把 HRV 拉爆
    if (abs(diff) > 300) {
      continue;
    }

    sumSq += diff * diff;
    n++;
  }

  if (n <= 0) return 0;
  return sqrt(sumSq / n);
}

bool isStableEnough(long ibi) {
  if (ibiCount < 3) return true;

  float avg = averageIBI();
  if (avg <= 0) return true;

  long diff = abs(ibi - avg);

  if (diff > 260) {
    return false;
  }

  return true;
}

String getQuality(float bpmValue, float rmssd) {
  if (ibiCount < 2) return "collecting";
  if (ibiCount < 4) return "quick";
  if (ibiCount < 8) return "preliminary";

  if (bpmValue < 50 || bpmValue > 120) return "unstable";
  if (rmssd < 0 || rmssd > 160) return "unstable";

  return "good";
}

String getEnergyState(float bpmValue, float rmssd, String quality) {
  if (quality == "collecting") return "syncing";

  if (quality == "quick") {
    if (bpmValue > 90) return "high_load_estimate";
    if (bpmValue < 60) return "low_activation_estimate";
    return "normal_estimate";
  }

  if (rmssd == 0) {
    if (bpmValue > 90) return "high_load_estimate";
    return "normal_estimate";
  }

  if (rmssd < 30 || bpmValue > 90) return "low_recovery";
  if (rmssd < 55) return "medium_recovery";
  return "good_recovery";
}

bool isDemoReady(String quality) {
  return quality == "quick" || quality == "preliminary" || quality == "good";
}

void printState(String label) {
  float bpmValue = calculateBPM();
  float rmssd = calculateRMSSD();

  if (bpmValue > 0) {
    lastValidBpm = bpmValue;
  }

  String quality = getQuality(bpmValue, rmssd);
  String energy = getEnergyState(bpmValue, rmssd, quality);
  bool demoReady = isDemoReady(quality);

  Serial.print(label);
  Serial.print(" | heart_rate=");
  Serial.print(bpmValue, 1);
  Serial.print(" | hrv_rmssd=");
  Serial.print(rmssd, 1);
  Serial.print(" | beats=");
  Serial.print(ibiCount);
  Serial.print(" | quality=");
  Serial.print(quality);
  Serial.print(" | energy_state=");
  Serial.print(energy);
  Serial.print(" | demo_ready=");
  Serial.println(demoReady ? "true" : "false");

  // 只有前端点了开始测试，并且数据达到 preliminary/good，才上传后端
  bool shouldPost = testActive &&
                    demoReady &&
                    rmssd > 0 &&
                    (quality == "preliminary" || quality == "good") &&
                    (millis() - lastPostTime > POST_INTERVAL);

  if (shouldPost) {
    postToBackend(bpmValue, rmssd);
    lastPostTime = millis();
  }
}

// ===============================
// WiFi
// ===============================
void connectWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startAttemptTime = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 20000) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected. ESP32 IP: ");
    Serial.println(WiFi.localIP());

    Serial.print("Backend URL: ");
    Serial.println(SERVER_URL);

    Serial.print("Command URL: ");
    Serial.println(COMMAND_URL);
  } else {
    Serial.println("WiFi connection failed. Command polling and POST will fail.");
  }
}

// ===============================
// Poll frontend command from backend
// ===============================
void pollBackendCommand() {
  unsigned long now = millis();

  if (now - lastCommandPollTime < COMMAND_POLL_INTERVAL) {
    return;
  }

  lastCommandPollTime = now;

  if (WiFi.status() != WL_CONNECTED) {
    Serial.print("COMMAND poll skipped | WiFi not connected | status=");
    Serial.println(WiFi.status());
    return;
  }

  Serial.print("Polling command URL: ");
  Serial.println(COMMAND_URL);

  HTTPClient http;
  http.setTimeout(2000);
  http.begin(COMMAND_URL);

  int httpCode = http.GET();

  Serial.print("Command GET httpCode=");
  Serial.println(httpCode);

  if (httpCode > 0) {
    String payload = http.getString();

    payload.replace(" ", "");
    payload.replace("\n", "");
    payload.replace("\r", "");

    Serial.print("COMMAND response: ");
    Serial.println(payload);

    if (payload.indexOf("\"command\":\"start\"") >= 0 && !testActive) {
      testActive = true;
      testStartTime = millis();
      lastPostTime = 0;

      resetMeasurement();
      fingerWasPresent = false;

      Serial.println("Command START received. Measurement started.");
    }

    if (payload.indexOf("\"command\":\"stop\"") >= 0 && testActive) {
      testActive = false;

      resetMeasurement();
      fingerWasPresent = false;

      Serial.println("Command STOP received. Measurement stopped.");
    }
  } else {
    Serial.println("Command GET failed. Cannot reach backend command endpoint.");
  }

  http.end();
}

// ===============================
// Setup
// ===============================
void setup() {
  Serial.begin(115200);
  delay(1000);

  connectWiFi();

  Wire.begin(SDA_PIN, SCL_PIN);

  if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 not found. Check wiring.");
    while (1);
  }

  byte ledBrightness = 0x1F;
  byte sampleAverage = 4;
  byte ledMode = 2;       // Red + IR
  int sampleRate = 100;
  int pulseWidth = 411;
  int adcRange = 8192;

  sensor.setup(
    ledBrightness,
    sampleAverage,
    ledMode,
    sampleRate,
    pulseWidth,
    adcRange
  );

  sensor.setPulseAmplitudeRed(RED_LED);
  sensor.setPulseAmplitudeIR(IR_LED);

  Serial.println("CycleSense Command-Controlled HRV Demo started.");
  Serial.println("Waiting for frontend start command...");
  Serial.println("Frontend button -> Backend /api/start-test -> ESP32 /api/device-command");
}

// ===============================
// Main loop
// ===============================
void loop() {
  // 重点：必须先轮询后端命令，再判断 IDLE
  pollBackendCommand();

  unsigned long now = millis();

  // ===============================
  // IDLE: 等前端按钮
  // ===============================
  if (!testActive) {
    if (now - lastStatusPrint > 1000) {
      Serial.println("IDLE | waiting for frontend start command...");
      lastStatusPrint = now;
    }

    delay(50);
    return;
  }

  // ===============================
  // 测试超时保护
  // ===============================
  if (now - testStartTime > TEST_TIMEOUT) {
    testActive = false;

    resetMeasurement();
    fingerWasPresent = false;

    Serial.println("Test timeout. Back to IDLE.");
    delay(50);
    return;
  }

  long irValue = sensor.getIR();

  // ---------- No finger ----------
  if (irValue < FINGER_THRESHOLD) {
    if (fingerWasPresent) {
      Serial.println("Finger removed. Waiting for finger...");
    }

    fingerWasPresent = false;
    resetMeasurement();

    if (now - lastStatusPrint > 700) {
      Serial.print("IR=");
      Serial.print(irValue);
      Serial.println(" | no_finger | test_active=true");
      lastStatusPrint = now;
    }

    delay(10);
    return;
  }

  // ---------- Finger just detected ----------
  if (!fingerWasPresent) {
    fingerWasPresent = true;
    fingerStartTime = now;
    lastQuickOutput = now;

    resetMeasurement();
    baseline = irValue;

    Serial.println("Finger detected. Starting body sync...");
    delay(1000);
    return;
  }

  updateSignalWindow(irValue);

  // ---------- Adaptive baseline ----------
  if (baseline <= 0) {
    baseline = irValue;
  }

  baseline = 0.98 * baseline + 0.02 * irValue;
  float ac = irValue - baseline;

  envelope = 0.97 * envelope + 0.03 * fabs(ac);

  // ---------- Final demo threshold ----------
  float threshold = envelope * 0.70;
  if (threshold < 300) threshold = 300;
  if (threshold > 3500) threshold = 3500;

  // ---------- Print signal status every second ----------
  if (now - lastStatusPrint > 1000) {
    long acRange = 0;
    if (sigCount > 0) {
      acRange = sigMax - sigMin;
    }

    Serial.print("SIGNAL");
    Serial.print(" | IR=");
    Serial.print(irValue);
    Serial.print(" | AC_range=");
    Serial.print(acRange);
    Serial.print(" | beats=");
    Serial.print(ibiCount);

    if (irValue > 240000) {
      Serial.print(" | signal=saturated");
    } else if (acRange < 500) {
      Serial.print(" | signal=weak_wave");
    } else {
      Serial.print(" | signal=usable_wave");
    }

    Serial.println();

    resetSignalWindow();
    lastStatusPrint = now;
  }

  // ---------- Custom peak detection ----------
  bool rising = ac > prevAc;
  bool peakDetected = false;

  if (wasRising && !rising && prevAc > threshold) {
    // 最小 430ms 间隔，减少假峰
    if (now - lastPeakTime > 430) {
      peakDetected = true;
      lastPeakTime = now;
    }
  }

  wasRising = rising;
  prevAc = ac;

  if (peakDetected) {
    if (lastBeatTime == 0) {
      lastBeatTime = now;
      Serial.println("First beat detected...");
    } else {
      long ibi = now - lastBeatTime;

      if (ibi < MIN_VALID_IBI) {
        Serial.print("Rejected short false peak IBI=");
        Serial.println(ibi);
      }

      else if (ibi > MAX_VALID_IBI) {
        Serial.print("Rejected long gap IBI=");
        Serial.println(ibi);
        lastBeatTime = now;
      }

      else if (!isStableEnough(ibi)) {
        Serial.print("Rejected jump IBI=");
        Serial.println(ibi);
        lastBeatTime = now;
      }

      else {
        lastBeatTime = now;
        addIBI(ibi);
        printState("STATE_UPDATE");
      }
    }
  }

  // ---------- No beat for too long ----------
  if (lastBeatTime != 0 && now - lastBeatTime > 3500) {
    lastBeatTime = 0;
    Serial.println("No beat for 3.5s. Re-syncing beat detector...");
  }

  // ---------- Quick estimate after 8 seconds ----------
  if (fingerWasPresent && now - fingerStartTime > 8000 && now - lastQuickOutput > 3000) {
    if (ibiCount > 0) {
      printState("QUICK_ESTIMATE");
    } else {
      Serial.println("QUICK_ESTIMATE | signal_detected=true | quality=weak | energy_state=syncing | demo_ready=false");
    }

    lastQuickOutput = now;
  }

  delay(10);
}

// ===============================
// POST data to backend
// ===============================
void postToBackend(float heartRate, float hrvRmssd) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected. Skip POST.");
    return;
  }

  HTTPClient http;
  http.setTimeout(1500);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  String json = "{";
  json += "\"heart_rate\":" + String((int)round(heartRate)) + ",";
  json += "\"hrv_rmssd\":" + String(hrvRmssd, 1);
  json += "}";

  int httpCode = http.POST(json);

  Serial.print("POST backend -> ");
  Serial.println(httpCode);
  Serial.println(json);

  if (httpCode == 200) {
    Serial.println("Backend received data successfully.");
  } else if (httpCode < 0) {
    Serial.println("POST failed: cannot reach backend. Check WiFi, IP, firewall, or backend server.");
  } else if (httpCode == 404) {
    Serial.println("POST failed: endpoint not found. Check /api/heartbeat path.");
  } else if (httpCode == 422) {
    Serial.println("POST failed: JSON format mismatch. Check backend schema.");
  }

  http.end();
}