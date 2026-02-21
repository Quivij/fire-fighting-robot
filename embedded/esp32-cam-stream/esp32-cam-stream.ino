/*
 * =====================================================
 * ESP32-CAM MJPEG STREAM SERVER - OPTIMIZED
 * =====================================================
 * Board: AI Thinker ESP32-CAM
 * Function: HTTP MJPEG Stream cho fire-fighting robot
 * 

 * Endpoints:
 *   - http://<ESP32-CAM-IP>/stream  → MJPEG video stream
 *   - http://<ESP32-CAM-IP>/capture → Single JPEG image
 *   - http://<ESP32-CAM-IP>/status  → Camera status JSON
 *
 * Upload instructions:
 *   1. GPIO0 → GND (enter flash mode)
 *   2. Reset ESP32-CAM
 *   3. Upload via FTDI (TX→RX, RX→TX, 5V→5V, GND→GND)
 *   4. Remove GPIO0 from GND, reset again
 * =====================================================
 */

 
#include "esp_camera.h"
#include "esp_wifi.h"
#include <WiFi.h>
#include <WebServer.h>
#include <WiFiClient.h>
#include "config.h"  // WiFi configuration

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
  config.xclk_freq_hz = 24000000;  // 24MHz for better FPS (was 20MHz)
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;  // Always get latest frame (skip old frames)

  // Frame size & quality - OPTIMIZED FOR 10-15 FPS STABLE
  if (psramFound()) {
    Serial.println("[CAMERA] PSRAM found");
    config.frame_size = FRAMESIZE_VGA;   // 640x480 - good balance
    config.jpeg_quality = JPEG_QUALITY_DEFAULT;  // From config.h
    config.fb_count = 2;                  // 2 buffers for smooth streaming
    config.fb_location = CAMERA_FB_IN_PSRAM;  // Use PSRAM for frame buffers
  } else {
    Serial.println("[CAMERA] No PSRAM - using QVGA");
    config.frame_size = FRAMESIZE_QVGA;  // 320x240
    config.jpeg_quality = JPEG_QUALITY_HIGH;  // Better quality for smaller frame
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
    // Camera settings tuning for best quality/performance
    s->set_brightness(s, 0);     // -2 to 2
    s->set_contrast(s, 0);       // -2 to 2
    s->set_saturation(s, 0);     // -2 to 2
    s->set_whitebal(s, 1);       // Auto white balance ON
    s->set_awb_gain(s, 1);       // Auto white balance gain ON
    s->set_exposure_ctrl(s, 1);  // Auto exposure ON
    s->set_aec2(s, 1);           // Auto exposure control 2 ON
    s->set_gain_ctrl(s, 1);      // Auto gain ON
    s->set_agc_gain(s, 0);       // Auto gain value (0-30)
    s->set_bpc(s, 1);            // Black pixel correction ON
    s->set_wpc(s, 1);            // White pixel correction ON
    s->set_lenc(s, 1);           // Lens correction ON
    s->set_hmirror(s, 0);        // Horizontal mirror
    s->set_vflip(s, 0);          // Vertical flip
    s->set_colorbar(s, 0);       // Disable test pattern
    
    Serial.println("[CAMERA] Sensor optimized for performance");
  }

  Serial.println("[CAMERA] Init OK!");
  return true;
}

// ===== HTTP HANDLERS =====

// MJPEG Stream handler - OPTIMIZED VERSION
void handleStream() {
  Serial.println("[HTTP] Stream client connected");

  WiFiClient client = server.client();
  
  // Configure client for low latency
  client.setNoDelay(true);  // Disable Nagle's algorithm
  
  // Send HTTP headers
  client.println("HTTP/1.1 200 OK");
  client.printf("Content-Type: %s\r\n", _STREAM_CONTENT_TYPE);
  client.println("Access-Control-Allow-Origin: *");
  client.println("Cache-Control: no-cache, no-store, must-revalidate");
  client.println("Pragma: no-cache");
  client.println("Expires: 0");
  // client.println("Connection: close");
  client.println("Connection: keep-alive");
  client.println();

  // Reset FPS counter
  frameCount = 0;
  lastFpsTime = millis();

  // Local buffer for content-length header (avoid String concatenation)
  char partHeader[64];

  // Stream loop - OPTIMIZED
  unsigned long lastFrameTime = millis();
  unsigned long clientCheckTime = millis();
  
  while (client.connected()) {
    //Check client alive every 10 seconds
    if (millis() - clientCheckTime >= 10000) {
      if (!client.available() && !client.connected()) {
        Serial.println("[HTTP] Client disconnected (timeout)");
        break;
      }
      clientCheckTime = millis();
    }

    // Get frame - LOCAL variable to prevent memory leak
    camera_fb_t* fb = esp_camera_fb_get();

    if (!fb) {
      Serial.println("[CAMERA] Frame capture failed!");
      delay(10);  // Small delay before retry
      continue;
    }

    // Validate frame
    if (fb->len == 0 || fb->buf == NULL) {
      Serial.println("[CAMERA] Invalid frame data!");
      esp_camera_fb_return(fb);  // CRITICAL: return immediately
      delay(10);
      continue;
    }

    // Calculate FPS
    frameCount++;
    unsigned long now = millis();
    if (now - lastFpsTime >= 1000) {
      currentFPS = frameCount * 1000.0 / (now - lastFpsTime);
      Serial.printf("[FPS] %.1f fps | Frame: %dx%d | Size: %u KB | Heap: %u KB\n", 
                    currentFPS, 
                    fb->width, 
                    fb->height,
                    fb->len / 1024,
                    ESP.getFreeHeap() / 1024);
      frameCount = 0;
      lastFpsTime = now;
    }

    // Send MJPEG boundary
    size_t boundaryLen = strlen(_STREAM_BOUNDARY);
    if (client.write(_STREAM_BOUNDARY, boundaryLen) != boundaryLen) {
      Serial.println("[HTTP] Failed to send boundary");
      esp_camera_fb_return(fb);
      break;
    }

    // Send part header with content length
    size_t headerLen = snprintf(partHeader, sizeof(partHeader), _STREAM_PART, fb->len);
    if (client.write(partHeader, headerLen) != headerLen) {
      Serial.println("[HTTP] Failed to send part header");
      esp_camera_fb_return(fb);
      break;
    }

    // Send JPEG data
    size_t sentBytes = client.write(fb->buf, fb->len);
    if (sentBytes != fb->len) {
      Serial.printf("[HTTP] Failed to send frame data (%u/%u bytes)\n", sentBytes, fb->len);
      esp_camera_fb_return(fb);
      break;
    }

    // CRITICAL: Return frame buffer IMMEDIATELY after sending
    esp_camera_fb_return(fb);
    fb = NULL;  // Prevent double-free

    lastFrameTime = now;

    // Yield to RTOS for other tasks (prevent watchdog timeout)
    vTaskDelay(1 / portTICK_PERIOD_MS);
  }

  Serial.println("[HTTP] Stream client disconnected");
}

// Single JPEG capture - OPTIMIZED
void handleCapture() {
  camera_fb_t* fb = esp_camera_fb_get();  // Local variable

  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    Serial.println("[HTTP] Capture failed - no frame");
    return;
  }

  // Validate frame
  if (fb->len == 0 || fb->buf == NULL) {
    esp_camera_fb_return(fb);
    server.send(500, "text/plain", "Invalid frame data");
    Serial.println("[HTTP] Capture failed - invalid frame");
    return;
  }

  server.sendHeader("Content-Disposition", "inline; filename=capture.jpg");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);

  esp_camera_fb_return(fb);  // CRITICAL: return frame buffer
  fb = NULL;

  Serial.println("[HTTP] Capture served");
}

// Status endpoint (JSON) - OPTIMIZED (avoid String concatenation)
void handleStatus() {
  // Use fixed buffer instead of String concatenation
  char json[256];
  snprintf(json, sizeof(json),
           "{\"camera\":\"online\","
           "\"wifi_rssi\":%d,"
           "\"uptime\":%lu,"
           "\"free_heap\":%u,"
           "\"fps\":%.1f}",
           WiFi.RSSI(),
           millis() / 1000,
           ESP.getFreeHeap(),
           currentFPS);

  server.send(200, "application/json", json);
  Serial.println("[HTTP] Status served");
}

// Root page - Lightweight HTML
void handleRoot() {
  const char* html = 
    "<html><head><title>ESP32-CAM Fire Robot</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "</head><body style='font-family:Arial;padding:20px;'>"
    "<h1>ESP32-CAM Stream Server</h1>"
    "<h2>Fire Fighting Robot</h2>"
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
  WiFi.setSleep(false);  // Disable WiFi sleep for better performance
  
  // Try each configured network
  for (int i = 0; i < WIFI_NETWORK_COUNT; i++) {
    Serial.print("[WiFi] Trying network ");
    Serial.print(i + 1);
    Serial.print(": ");
    Serial.println(wifiCredentials[i][0]);
    
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
      Serial.print("[WiFi] Network: ");
      Serial.println(wifiCredentials[i][0]);
      Serial.print("[WiFi] IP Address: ");
      Serial.println(WiFi.localIP());
      Serial.print("[WiFi] Signal: ");
      Serial.print(WiFi.RSSI());
      Serial.println(" dBm");
      
      // Disable WiFi power save COMPLETELY for stable FPS
      esp_wifi_set_ps(WIFI_PS_NONE);
      Serial.println("[WiFi] Power save DISABLED");
      return;  // Success, exit function
    } else {
      Serial.println(" failed");
    }
  }
  
  // All networks failed
  Serial.println("\n[WiFi] ✗ All networks FAILED!");
  Serial.println("[WiFi] Please check credentials in config.h");
  connectedNetworkIndex = -1;
}

// ===== SETUP =====

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n\n===========================================");
  Serial.println("  ESP32-CAM MJPEG STREAM SERVER");
  Serial.println("  Fire Fighting Robot - Camera Module");
  Serial.println("  OPTIMIZED VERSION - No Memory Leaks");
  Serial.println("===========================================\n");

  // PSRAM check
  if (psramFound()) {
    Serial.printf("[PSRAM] Found: %u bytes\n", ESP.getPsramSize());
  } else {
    Serial.println("[PSRAM] Not found - performance may be limited");
  }

  // Initialize camera
  if (!initCamera()) {
    Serial.println("[ERROR] Camera init failed! Halting.");
    while (1) delay(1000);
  }

  // Connect WiFi
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

  // Start server
  server.begin();
  Serial.println("\n[HTTP] Server started!");
  Serial.println("===========================================");
  Serial.println("  Endpoints:");
  Serial.print("  - Stream:  http://");
  Serial.print(WiFi.localIP());
  Serial.println("/stream");
  Serial.print("  - Capture: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture");
  Serial.print("  - Status:  http://");
  Serial.print(WiFi.localIP());
  Serial.println("/status");
  Serial.println("===========================================\n");
  Serial.println("  SYSTEM READY!");
  Serial.printf("  Free Heap: %u KB\n", ESP.getFreeHeap() / 1024);
  Serial.println("===========================================\n");
}

// ===== MAIN LOOP =====

void loop() {
  server.handleClient();  // Handle HTTP requests
  
  // Small delay for RTOS task scheduling (prevent watchdog)
  vTaskDelay(1 / portTICK_PERIOD_MS);
}