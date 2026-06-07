/*
 * =====================================================
 * ESP32-CAM MJPEG STREAM SERVER - RTOS OPTIMIZED
 * =====================================================
 * Board: AI Thinker ESP32-CAM
 * Function: HTTP MJPEG Stream cho fire-fighting robot
 * 
 * RTOS Architecture:
 * - Task 1 (Core 0): WebServer Task - Xử lý HTTP & Stream (Có thể block khi stream)
 * - Task 2 (Core 1): Robot Control Task - Điều khiển motor/cảm biến độc lập
 * =====================================================
 */

#include "esp_camera.h"
#include "esp_wifi.h"
#include <WiFi.h>
#include <WebServer.h>
#include <WiFiClient.h>
#include "config.h"  // WiFi configuration
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// ===== CAMERA PINS (AI-Thinker ESP32-CAM) =====
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ===== WIFI CONFIGURATION =====
// WiFi credentials array for multi-network support
const char* wifiCredentials[][2] = {
  {WIFI_SSID_1, WIFI_PASS_1},
  {WIFI_SSID_2, WIFI_PASS_2}
};

// Track which network is connected
int connectedNetworkIndex = -1;

// ===== WEB SERVER =====
WebServer server(80);

// ===== FPS TRACKING =====
unsigned long frameCount = 0;
unsigned long lastFpsTime = 0;
float currentFPS = 0.0;

// ===== RTOS TASK HANDLES =====
TaskHandle_t serverTaskHandle = NULL;
TaskHandle_t robotTaskHandle  = NULL;

// ===== PRE-ALLOCATED BUFFERS (avoid heap fragmentation) =====
#define PART_BOUNDARY "frame"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// ===== CAMERA INITIALIZATION =====

bool initCamera() {
  Serial.println("[CAMERA] Initializing...");

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 24000000;  // 24MHz for better FPS
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;

  // Frame size & quality - OPTIMIZED
  if (psramFound()) {
    Serial.println("[CAMERA] PSRAM found");
    config.frame_size = FRAMESIZE_VGA;   // 640x480
    config.jpeg_quality = JPEG_QUALITY_DEFAULT;  // From config.h
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("[CAMERA] No PSRAM - using QVGA");
    config.frame_size = FRAMESIZE_QVGA;  // 320x240
    config.jpeg_quality = JPEG_QUALITY_HIGH;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  // Init camera
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAMERA] Init FAILED! Error: 0x%x\n", err);
    return false;
  }

  // Get sensor for adjustments
  sensor_t* s = esp_camera_sensor_get();
  if (s != NULL) {
    s->set_brightness(s, 0);
    s->set_contrast(s, 0);
    s->set_saturation(s, 0);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_aec2(s, 1);
    s->set_gain_ctrl(s, 1);
    s->set_agc_gain(s, 0);
    s->set_bpc(s, 1);
    s->set_wpc(s, 1);
    s->set_lenc(s, 1);
    s->set_hmirror(s, 0);
    s->set_vflip(s, 0);
    s->set_colorbar(s, 0);
    
    Serial.println("[CAMERA] Sensor optimized for performance");
  }

  Serial.println("[CAMERA] Init OK!");
  return true;
}

// ===== HTTP HANDLERS =====

void handleStream() {
  Serial.println("[HTTP] Stream client connected");

  WiFiClient client = server.client();
  client.setNoDelay(true);
  
  client.println("HTTP/1.1 200 OK");
  client.printf("Content-Type: %s\r\n", _STREAM_CONTENT_TYPE);
  client.println("Access-Control-Allow-Origin: *");
  client.println("Cache-Control: no-cache, no-store, must-revalidate");
  client.println("Pragma: no-cache");
  client.println("Expires: 0");
  client.println("Connection: keep-alive");
  client.println();

  frameCount = 0;
  lastFpsTime = millis();
  char partHeader[64];

  unsigned long lastFrameTime = millis();
  unsigned long clientCheckTime = millis();
  
  while (client.connected()) {
    if (millis() - clientCheckTime >= 10000) {
      if (!client.available() && !client.connected()) {
        Serial.println("[HTTP] Client disconnected (timeout)");
        break;
      }
      clientCheckTime = millis();
    }

    camera_fb_t* fb = esp_camera_fb_get();

    if (!fb) {
      Serial.println("[CAMERA] Frame capture failed!");
      vTaskDelay(10 / portTICK_PERIOD_MS);
      continue;
    }

    if (fb->len == 0 || fb->buf == NULL) {
      Serial.println("[CAMERA] Invalid frame data!");
      esp_camera_fb_return(fb);
      vTaskDelay(10 / portTICK_PERIOD_MS);
      continue;
    }

    frameCount++;
    unsigned long now = millis();
    if (now - lastFpsTime >= 1000) {
      currentFPS = frameCount * 1000.0 / (now - lastFpsTime);
      Serial.printf("[FPS] %.1f fps | Size: %u KB\n", currentFPS, fb->len / 1024);
      frameCount = 0;
      lastFpsTime = now;
    }

    size_t boundaryLen = strlen(_STREAM_BOUNDARY);
    if (client.write(_STREAM_BOUNDARY, boundaryLen) != boundaryLen) {
      Serial.println("[HTTP] Failed to send boundary");
      esp_camera_fb_return(fb);
      break;
    }

    size_t headerLen = snprintf(partHeader, sizeof(partHeader), _STREAM_PART, fb->len);
    if (client.write(partHeader, headerLen) != headerLen) {
      Serial.println("[HTTP] Failed to send part header");
      esp_camera_fb_return(fb);
      break;
    }

    size_t sentBytes = client.write(fb->buf, fb->len);
    if (sentBytes != fb->len) {
      Serial.println("[HTTP] Failed to send frame data");
      esp_camera_fb_return(fb);
      break;
    }

    esp_camera_fb_return(fb);
    fb = NULL;
    lastFrameTime = now;

    // Yield to RTOS to prevent watchdog timeout
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }

  Serial.println("[HTTP] Stream client disconnected");
}

void handleCapture() {
  camera_fb_t* fb = esp_camera_fb_get();

  if (!fb || fb->len == 0 || fb->buf == NULL) {
    if (fb) esp_camera_fb_return(fb);
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }

  server.sendHeader("Content-Disposition", "inline; filename=capture.jpg");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);

  esp_camera_fb_return(fb);
  Serial.println("[HTTP] Capture served");
}

void handleStatus() {
  char json[256];
  snprintf(json, sizeof(json),
           "{\"camera\":\"online\",\"wifi_rssi\":%d,\"uptime\":%lu,\"free_heap\":%u,\"fps\":%.1f}",
           WiFi.RSSI(), millis() / 1000, ESP.getFreeHeap(), currentFPS);

  server.send(200, "application/json", json);
}

void handleRoot() {
  const char* html = 
    "<html><head><title>ESP32-CAM Fire Robot (RTOS)</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "</head><body style='font-family:Arial;padding:20px;'>"
    "<h1>ESP32-CAM Stream Server</h1>"
    "<h2>Fire Fighting Robot (RTOS Edition)</h2>"
    "<p><a href='/stream'>MJPEG Stream</a></p>"
    "<p><a href='/capture'>Single Capture</a></p>"
    "<p><a href='/status'>Status JSON</a></p>"
    "<hr><img src='/stream' style='width:100%;max-width:800px;'>"
    "</body></html>";

  server.send(200, "text/html", html);
}

// ===== WIFI =====

void connectWiFi() {
  Serial.println("\n[WiFi] Connecting...");
  
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  
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
      esp_wifi_set_ps(WIFI_PS_NONE);
      return;
    }
  }
  
  Serial.println("\n[WiFi] ✗ All networks FAILED!");
}

// ===== RTOS TASKS =====

// Task 1: Xử lý Web Server (Chạy trên Core 0 - Chuyên Network)
void serverTask(void *pvParameters) {
  Serial.println("[RTOS] Server Task started on Core 0");
  
  // Vòng lặp vô hạn của Task
  while (1) {
    server.handleClient();  // Xử lý các HTTP request
    
    // Rất quan trọng: Phải có delay/yield để hệ điều hành chuyển đổi sang task khác
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// Task 2: Điều khiển Robot Cứu Hỏa (Chạy trên Core 1 - Chuyên Application)
void robotControlTask(void *pvParameters) {
  Serial.println("[RTOS] Robot Control Task started on Core 1");
  
  // TODO: Khởi tạo các chân IO động cơ, cảm biến lửa ở đây
  // pinMode(MOTOR_PIN, OUTPUT);
  // pinMode(FLAME_SENSOR, INPUT);
  
  // Vòng lặp vô hạn của Task Robot
  while (1) {
    // TODO: Viết logic điều khiển robot ở đây
    // Ví dụ: Đọc cảm biến lửa -> Điều khiển động cơ tới dập lửa
    // Các lệnh trong này sẽ chạy liên tục, ĐỘC LẬP và KHÔNG BỊ GIẬT LAG khi có người vào xem Camera (Stream)
    
    // Mô phỏng logic đang chạy
    // Serial.println("[ROBOT] Đang kiểm tra cảm biến...");
    
    vTaskDelay(50 / portTICK_PERIOD_MS); // Chạy 20 vòng/giây
  }
}

// ===== SETUP =====

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n\n===========================================");
  Serial.println("  ESP32-CAM MJPEG STREAM SERVER (RTOS)");
  Serial.println("  Fire Fighting Robot - Camera Module");
  Serial.println("===========================================\n");

  if (!initCamera()) {
    Serial.println("[ERROR] Camera init failed! Halting.");
    while (1) delay(1000);
  }

  connectWiFi();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[ERROR] WiFi failed! Halting.");
    while (1) delay(1000);
  }

  // Setup HTTP server endpoints
  server.on("/", handleRoot);
  server.on("/stream", handleStream);
  server.on("/capture", handleCapture);
  server.on("/status", handleStatus);
  server.begin();

  Serial.println("\n[HTTP] Server started!");
  
  // ==========================================
  // KHỞI TẠO FREERTOS TASKS
  // ==========================================
  
  // 1. Tạo Server Task chạy ở Core 0
  xTaskCreatePinnedToCore(
    serverTask,         // Tên hàm thực thi
    "ServerTask",       // Tên Task (dùng để debug)
    4096,               // Kích thước bộ nhớ stack (bytes)
    NULL,               // Tham số truyền vào (không có)
    1,                  // Mức độ ưu tiên (Priority 1)
    &serverTaskHandle,  // Con trỏ lưu Task Handle
    0                   // Core 0
  );

  // 2. Tạo Robot Control Task chạy ở Core 1
  xTaskCreatePinnedToCore(
    robotControlTask,   // Tên hàm thực thi
    "RobotTask",        // Tên Task (dùng để debug)
    4096,               // Kích thước bộ nhớ stack (bytes)
    NULL,               // Tham số truyền vào (không có)
    1,                  // Mức độ ưu tiên (Priority 1)
    &robotTaskHandle,   // Con trỏ lưu Task Handle
    1                   // Core 1
  );

  Serial.println("===========================================");
  Serial.println("  SYSTEM READY! RTOS Tasks are running.");
  Serial.println("===========================================\n");
}

// ===== MAIN LOOP =====

void loop() {
  // Vì chúng ta đã sử dụng FreeRTOS Tasks trong hàm setup()
  // Hàm loop() mặc định của Arduino (cũng là một Task) không còn cần thiết nữa.
  // Xóa Task này để giải phóng bộ nhớ và tài nguyên cho ESP32.
  vTaskDelete(NULL);
}