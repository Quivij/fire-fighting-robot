/*
 * ESP32 MQTT CONTROL - FIRE FIGHTING ROBOT (RTOS VERSION)
 * Chức năng: Điều khiển motor + pump qua MQTT + Sensors
 * Linh kiện:
 *   - ESP32 Wroom-32
 *   - L298N Motor Driver
 *   - 4x DC Motor 3V
 *   - Relay + Water Pump 12V
 *   - HC-SR04 Ultrasonic Sensor (Distance)
 *   - Flame Detector (Analog + Digital)
 *
 * RTOS Architecture:
 * - mqttMutex: Tránh đụng độ (Race Condition) khi gọi thư viện MQTT từ nhiều Task
 * - Task 1 (Core 0): mqttTask - Kết nối WiFi, MQTT, lắng nghe lệnh điều khiển và Publish Status
 * - Task 2 (Core 1): sensorTask - Đọc cảm biến Ultrasonic/Flame và Publish lên MQTT
 * - Task 3 (Core 1): safetyTask - Tự động dừng xe nếu mất kết nối MQTT quá lâu
 */

//  INCLUDE LIBRARIES 
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "config.h"  // WiFi & MQTT configuration
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

// CONFIGURATION 

// WiFi credentials array for multi-network support
const char* wifiCredentials[][2] = {
  {WIFI_SSID_1, WIFI_PASS_1},
  {WIFI_SSID_2, WIFI_PASS_2}
};

const char* mqttBrokers[] = {
  MQTT_BROKER_1,
  MQTT_BROKER_2
};

// Track which network is connected
int connectedNetworkIndex = -1;

// MQTT Topics
const char* TOPIC_MOTOR_CONTROL = "robot/control/motor";
const char* TOPIC_PUMP_CONTROL = "robot/control/pump";
const char* TOPIC_STATUS = "robot/status";
const char* TOPIC_SENSOR_DISTANCE = "robot/sensors/distance";
const char* TOPIC_SENSOR_FLAME = "robot/sensors/flame";

// PIN DEFINITIONS

// ===== MOTOR CONTROL PINS (2x L298N – 4 MOTORS) =====

// L298N LEFT (2 bánh trái)
#define L_ENA 25
#define L_IN1 12
#define L_IN2 13
#define L_IN3 14
#define L_IN4 27
#define L_ENB 26

// L298N RIGHT (2 bánh phải)
#define R_ENA 33
#define R_IN1 16
#define R_IN2 17
#define R_IN3 5
#define R_IN4 21
#define R_ENB 32

// Pump Control
 #define PUMP_RELAY 23

// Sensor Pins
#define ULTRASONIC_TRIG 18  // HC-SR04 Trigger
#define ULTRASONIC_ECHO 35  // HC-SR04 Echo1
#define FLAME_DIGITAL 4     // Flame Sensor Digital Output

// LED Built-in
 #define LED_BUILTIN 2 

// PWM SETTINGS
#define PWM_CHANNEL_LEFT 0
#define PWM_CHANNEL_RIGHT 1
#define PWM_FREQ 1000           // 1kHz PWM frequency
#define PWM_RESOLUTION 8        // 8-bit resolution (0-255)

// GLOBAL VARIABLES
#define PWM_LEFT_CALIBRATION 1.00
#define PWM_RIGHT_CALIBRATION 0.85

// WiFi & MQTT Clients
WiFiClient espClient;
PubSubClient mqtt(espClient);

// RTOS Task Handles & Mutex
TaskHandle_t mqttTaskHandle   = NULL;
TaskHandle_t sensorTaskHandle = NULL;
TaskHandle_t safetyTaskHandle = NULL;
SemaphoreHandle_t mqttMutex;

// Robot State
String currentMotorState = "stop";
int currentSpeed = 0;
int currentPWM = 0;
volatile unsigned long lastCommandTime = 0; // Thêm volatile để chống lỗi tối ưu hóa compiler

bool pumpState = false;

// Timing Variables
unsigned long lastStatusUpdate = 0;
unsigned long lastWiFiCheck = 0;
unsigned long lastSensorUpdate = 0;

// Connection Status
bool mqttConnected = false;

// Sensor Data
float currentDistance = 0.0;
bool currentFlameDigital = false;

// MOTOR CONTROL FUNCTIONS

void motorStop() {
  digitalWrite(L_IN1, LOW); digitalWrite(L_IN2, LOW);
  digitalWrite(L_IN3, LOW); digitalWrite(L_IN4, LOW);

  digitalWrite(R_IN1, LOW); digitalWrite(R_IN2, LOW);
  digitalWrite(R_IN3, LOW); digitalWrite(R_IN4, LOW);

  ledcWrite(L_ENA, 0);
  ledcWrite(L_ENB, 0);

  ledcWrite(R_ENA, 0);
  ledcWrite(R_ENB, 0);

  currentMotorState = "stop";
  currentSpeed = 0;
  Serial.println("[MOTOR] Stop");
}

void setupMotors() {
  Serial.println("[SETUP] Initializing 4 motors (2x L298N)...");

  int pins[] = {
    L_IN1, L_IN2, L_IN3, L_IN4,
    R_IN1, R_IN2, R_IN3, R_IN4
  };

  for (int i = 0; i < 8; i++) {
    pinMode(pins[i], OUTPUT);
    digitalWrite(pins[i], LOW);
  }

  // ESP32 Core 3.x PWM
  ledcAttach(L_ENA, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(L_ENB, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(R_ENA, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(R_ENB, PWM_FREQ, PWM_RESOLUTION);

  ledcWrite(L_ENA, 0);
  ledcWrite(L_ENB, 0);
  ledcWrite(R_ENA, 0);
  ledcWrite(R_ENB, 0);

  motorStop();
  Serial.println("[SETUP] Motors ready");
}

void motorForward(uint8_t speed) {
  digitalWrite(L_IN1, HIGH);  
  digitalWrite(L_IN2, LOW);
  digitalWrite(L_IN3, HIGH);  
  digitalWrite(L_IN4, LOW);

  digitalWrite(R_IN1, HIGH);  
  digitalWrite(R_IN2, LOW);
  digitalWrite(R_IN3, HIGH);  
  digitalWrite(R_IN4, LOW);

  ledcWrite(L_ENA, speed);
  ledcWrite(L_ENB, speed);
  ledcWrite(R_ENA, speed);
  ledcWrite(R_ENB, speed);

  currentMotorState = "forward";
  currentSpeed = speed;
  Serial.printf("[MOTOR] Forward - Speed: %d\n", speed);
}

void motorBackward(uint8_t speed) {
  digitalWrite(L_IN1, LOW);  
  digitalWrite(L_IN2, HIGH);
  digitalWrite(L_IN3, LOW);  
  digitalWrite(L_IN4, HIGH);

  digitalWrite(R_IN1, LOW);  
  digitalWrite(R_IN2, HIGH);
  digitalWrite(R_IN3, LOW);  
  digitalWrite(R_IN4, HIGH);

  ledcWrite(L_ENA, speed);
  ledcWrite(L_ENB, speed);
  ledcWrite(R_ENA, speed);
  ledcWrite(R_ENB, speed);

  currentMotorState = "backward";
  currentSpeed = speed;
  Serial.printf("[MOTOR] Backward - Speed: %d\n", speed);
}

void motorLeft(uint8_t speed) {
  uint8_t slowSpeed = speed * 0.4;

  digitalWrite(L_IN1, HIGH);  
  digitalWrite(L_IN2, LOW);
  digitalWrite(L_IN3, HIGH);  
  digitalWrite(L_IN4, LOW);

  digitalWrite(R_IN1, LOW);   
  digitalWrite(R_IN2, HIGH);
  digitalWrite(R_IN3, LOW);
  digitalWrite(R_IN4, HIGH);

  ledcWrite(L_ENA, slowSpeed);
  ledcWrite(L_ENB, slowSpeed);
  ledcWrite(R_ENA, speed);
  ledcWrite(R_ENB, speed);

  currentMotorState = "left";
  currentSpeed = speed;
}

void motorRight(uint8_t speed) {
  uint8_t slowSpeed = speed * 0.4;

  digitalWrite(L_IN1, HIGH);  
  digitalWrite(L_IN2, LOW);
  digitalWrite(L_IN3, HIGH);  
  digitalWrite(L_IN4, LOW);

  digitalWrite(R_IN1, LOW);   
  digitalWrite(R_IN2, HIGH);
  digitalWrite(R_IN3, LOW);
  digitalWrite(R_IN4, HIGH);

  ledcWrite(L_ENA, speed);
  ledcWrite(L_ENB, speed);
  ledcWrite(R_ENA, slowSpeed);
  ledcWrite(R_ENB, slowSpeed);

  currentMotorState = "right";
  currentSpeed = speed;
}

// PUMP CONTROL FUNCTIONS
void setupPump() {
  Serial.println("[SETUP] Initializing pump...");
  pinMode(PUMP_RELAY, OUTPUT);
  digitalWrite(PUMP_RELAY, HIGH); // OFF (relay active LOW)
  pumpState = false;
  Serial.println("[SETUP] Pump OFF");
}

void pumpOn() {
  digitalWrite(PUMP_RELAY, LOW); // ON
  pumpState = true;
  Serial.println("[PUMP] ON");
}

void pumpOff() {
  digitalWrite(PUMP_RELAY, HIGH); // OFF
  pumpState = false;
  Serial.println("[PUMP] OFF");
}

void setupSensors() {
  Serial.println("[SETUP] Initializing sensors...");
  pinMode(ULTRASONIC_TRIG, OUTPUT);
  pinMode(ULTRASONIC_ECHO, INPUT);
  digitalWrite(ULTRASONIC_TRIG, LOW);
  pinMode(FLAME_DIGITAL, INPUT); 
  Serial.println("[SETUP] Sensors initialized");
}

float readDistance() {
  digitalWrite(ULTRASONIC_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(ULTRASONIC_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(ULTRASONIC_TRIG, LOW);

  long duration = pulseIn(ULTRASONIC_ECHO, HIGH, 30000);
  float distance = duration / 58.0;

  if (distance < 2.0 || distance > 400.0) {
    return -1.0; 
  }
  return distance;
}

void readFlame() {
  currentFlameDigital = (digitalRead(FLAME_DIGITAL) == LOW);
}

float readDistanceFiltered() {
  float sum = 0;
  int count = 0;

  for (int i = 0; i < 3; i++) {
    float d = readDistance();
    if (d > 0) {
      sum += d;
      count++;
    }
    delay(5);
  }

  return count ? sum / count : -1;
}

// SENSOR PUBLISH (RTOS SAFE)
void publishSensorData() {
  if (!mqttConnected) return;

  // Read sensors without Mutex (Local hardware read)
  currentDistance = readDistanceFiltered();
  readFlame();

  // Protect MQTT Publish
  if (xSemaphoreTake(mqttMutex, portMAX_DELAY) == pdTRUE) {
    
    // ===== DISTANCE =====
    if (currentDistance > 0) {
      StaticJsonDocument<128> distDoc;
      distDoc["distance"] = currentDistance;
      distDoc["unit"] = "cm";
      distDoc["timestamp"] = millis();

      char distBuffer[128];
      serializeJson(distDoc, distBuffer);
      mqtt.publish(TOPIC_SENSOR_DISTANCE, distBuffer);

      Serial.printf("[SENSOR] Distance: %.1f cm\n", currentDistance);
    } else {
      Serial.println("[SENSOR] Distance: Out of range");
    }

    // ===== FLAME =====
    StaticJsonDocument<128> flameDoc;
    flameDoc["detected"] = currentFlameDigital;
    flameDoc["timestamp"] = millis();

    char flameBuffer[128];
    serializeJson(flameDoc, flameBuffer);
    mqtt.publish(TOPIC_SENSOR_FLAME, flameBuffer);

    Serial.printf("[SENSOR] Flame: %s\n", 
                  currentFlameDigital ? "🔥 FIRE DETECTED" : "No fire");

    xSemaphoreGive(mqttMutex);
  }
}

// WiFi FUNCTIONS

void connectWiFi() {
  Serial.println("\n[WiFi] Connecting to WiFi...");
  WiFi.mode(WIFI_STA);
  
  for (int i = 0; i < WIFI_NETWORK_COUNT; i++) {
    Serial.printf("[WiFi] Trying network %d: %s\n", i + 1, wifiCredentials[i][0]);
    WiFi.begin(wifiCredentials[i][0], wifiCredentials[i][1]);
    
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
      delay(500);
      Serial.print(".");
      attempts++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
      connectedNetworkIndex = i;
      Serial.println("\n[WiFi] ✓ Connected!");
      Serial.print("[WiFi] IP Address: ");
      Serial.println(WiFi.localIP());
      return; 
    } else {
      Serial.println(" failed");
    }
  }
  
  Serial.println("\n[WiFi] ✗ All networks FAILED!");
  connectedNetworkIndex = -1;
}

void checkWiFi() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] ✗ Disconnected! Reconnecting...");
    connectWiFi();
  }
}

// MQTT FUNCTIONS

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // Được gọi trong mqttTask, không cần lấy thêm Mutex
  Serial.print("[MQTT] ← Message on topic: ");
  Serial.println(topic);

  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, payload, length);

  if (error) {
    Serial.print("[MQTT] ✗ JSON parse error: ");
    Serial.println(error.c_str());
    return;
  }

  if (strcmp(topic, TOPIC_MOTOR_CONTROL) == 0) {
    const char* action = doc["action"];
    int speed = doc["speed"] | DEFAULT_MOTOR_SPEED;
    
    speed = constrain(speed, MIN_MOTOR_SPEED, MAX_MOTOR_SPEED);
    lastCommandTime = millis(); // Cập nhật thời gian nhận lệnh (Failsafe)

    if (strcmp(action, "forward") == 0) {
      motorForward(speed);
    }
    else if (strcmp(action, "backward") == 0) {
      motorBackward(speed);
    }
    else if (strcmp(action, "left") == 0) {
      motorLeft(speed);
    }
    else if (strcmp(action, "right") == 0) {
      motorRight(speed);
    }
    else if (strcmp(action, "stop") == 0) {
      motorStop();
    }
    else {
      Serial.printf("[MOTOR] Unknown action: %s\n", action);
    }
  }
  else if (strcmp(topic, TOPIC_PUMP_CONTROL) == 0) {
    const char* state = doc["state"];
    if (strcmp(state, "on") == 0) {
      pumpOn();
    }
    else if (strcmp(state, "off") == 0) {
      pumpOff();
    }
    else {
      Serial.println("[PUMP] Unknown state");
    }
  }
}

void connectMQTT() {
  const char* broker = (connectedNetworkIndex >= 0) ? 
                       mqttBrokers[connectedNetworkIndex] : 
                       MQTT_BROKER_1;
  
  Serial.print("[MQTT] Connecting to broker ");
  Serial.print(broker);
  Serial.print("...");

  if (mqtt.connect(MQTT_CLIENT_ID)) {
    Serial.println(" Connected!");
    mqtt.subscribe(TOPIC_MOTOR_CONTROL);
    mqtt.subscribe(TOPIC_PUMP_CONTROL);
    
    Serial.println("[MQTT] Subscribed to control topics");
    mqttConnected = true;

    for (int i = 0; i < 3; i++) {
      digitalWrite(LED_BUILTIN, HIGH);
      delay(100);
      digitalWrite(LED_BUILTIN, LOW);
      delay(100);
    }
  } else {
    Serial.print(" Failed! rc=");
    Serial.print(mqtt.state());
    Serial.println(" | Retrying...");
    mqttConnected = false;
  }
}

void publishStatus() {
  if (!mqttConnected) return;

  StaticJsonDocument<256> doc;
  doc["motor"] = currentMotorState;
  doc["motor_speed"] = currentSpeed;
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["uptime"] = millis() / 1000;
  doc["free_heap"] = ESP.getFreeHeap();
  doc["mqtt_connected"] = mqttConnected;

  char buffer[256];
  serializeJson(doc, buffer);

  if (xSemaphoreTake(mqttMutex, portMAX_DELAY) == pdTRUE) {
    bool success = mqtt.publish(TOPIC_STATUS, buffer);
    if (success) {
      Serial.print("[MQTT] → Status published: ");
      Serial.println(buffer);
    } else {
      Serial.println("[MQTT] Failed to publish status");
    }
    xSemaphoreGive(mqttMutex);
  }
}

// ===== RTOS TASKS =====

// Task 1 (Core 0): Xử lý kết nối WiFi và MQTT
void mqttTask(void *pvParameters) {
  Serial.println("[RTOS] MQTT Task started on Core 0");
  
  while (1) {
    if (millis() - lastWiFiCheck > WIFI_CHECK_INTERVAL) {
      checkWiFi();
      lastWiFiCheck = millis();
    }

    if (WiFi.status() == WL_CONNECTED) {
      // Bảo vệ các thao tác thư viện PubSubClient bằng Mutex
      if (xSemaphoreTake(mqttMutex, portMAX_DELAY) == pdTRUE) {
        if (!mqtt.connected()) {
          mqttConnected = false;
          connectMQTT();
        } else {
          mqttConnected = true;
          mqtt.loop(); // Lắng nghe gói tin mới từ broker
        }
        xSemaphoreGive(mqttMutex); // Luôn phải trả Mutex
      }
    } else {
      mqttConnected = false;
    }

    if (millis() - lastStatusUpdate >= STATUS_INTERVAL) {
      publishStatus();
      lastStatusUpdate = millis();
    }

    vTaskDelay(20 / portTICK_PERIOD_MS);
  }
}

// Task 2 (Core 1): Đọc cảm biến
void sensorTask(void *pvParameters) {
  Serial.println("[RTOS] Sensor Task started on Core 1");

  while (1) {
    if (millis() - lastSensorUpdate >= SENSOR_INTERVAL) {
      publishSensorData();
      lastSensorUpdate = millis();
    }
    vTaskDelay(50 / portTICK_PERIOD_MS);
  }
}

// Task 3 (Core 1): An toàn / Failsafe
void safetyTask(void *pvParameters) {
  Serial.println("[RTOS] Safety Task started on Core 1");

  while (1) {
    // 🚨 FAILSAFE: Nếu mất điều khiển / mất kết nối MQTT quá lâu thì dừng toàn bộ hệ thống
    if (millis() - lastCommandTime > COMMAND_TIMEOUT) {
      if (currentMotorState != "stop" || pumpState != false) {
        Serial.println("[SAFETY] Timeout detected! Stopping motors and pump...");
        motorStop();
        pumpOff();
      }
    }
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }
}

// ===== SETUP =====

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n\n");
  Serial.println("  FIRE FIGHTING ROBOT - ESP32");
  Serial.println("  MQTT Control System (RTOS VERSION)");
  Serial.println("-----------------------------------------\n");

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  setupMotors();
  setupPump();
  setupSensors();
  delay(100);

  connectWiFi();

  const char* broker = (connectedNetworkIndex >= 0) ? 
                       mqttBrokers[connectedNetworkIndex] : 
                       MQTT_BROKER_1;
  mqtt.setServer(broker, MQTT_PORT);
  mqtt.setCallback(mqttCallback);

  // Khởi tạo Mutex để chống đụng độ giữa mqttTask và sensorTask
  mqttMutex = xSemaphoreCreateMutex();
  if (mqttMutex == NULL) {
    Serial.println("[ERROR] Cannot create mqttMutex! Halting.");
    while(1) delay(1000);
  }

  // KHỞI TẠO CÁC FREERTOS TASKS
  
  xTaskCreatePinnedToCore(
    mqttTask, "MqttTask", 4096, NULL, 2, &mqttTaskHandle, 0
  );

  xTaskCreatePinnedToCore(
    sensorTask, "SensorTask", 4096, NULL, 1, &sensorTaskHandle, 1
  );

  xTaskCreatePinnedToCore(
    safetyTask, "SafetyTask", 2048, NULL, 3, &safetyTaskHandle, 1
  );

  Serial.println("  SYSTEM READY! RTOS Tasks are running.");
  Serial.println("Waiting for MQTT commands...\n");
}

void loop() {
  vTaskDelete(NULL); // Hủy Task loop() gốc
}