"""
Configuration file for Backend Server
Centralized configuration management
"""

# Flask Server Configuration
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
SECRET_KEY = "fire-robot-secret-2025"

# MQTT Broker Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60
MQTT_CLIENT_ID = "FireRobotBackend"

# MQTT Topics
TOPIC_MOTOR_CONTROL = "robot/control/motor"
TOPIC_PUMP_CONTROL = "robot/control/pump"
TOPIC_STATUS = "robot/status"
TOPIC_SENSOR_DISTANCE = "robot/sensors/distance"
TOPIC_SENSOR_FLAME = "robot/sensors/flame"

# ESP32-CAM Configuration
ESP32_CAM_URL = "http://172.18.4.87"  # IP từ Serial Monitor

# AI Performance Configuration
# Adjust these based on your CPU performance:
# - Fast CPU (i7/i9): process_every_n_frames = 2 (~15 FPS AI)
# - Medium CPU (i5): process_every_n_frames = 3 (~10 FPS AI)  [DEFAULT]
# - Slow CPU (i3/Celeron): process_every_n_frames = 5 (~6 FPS AI)
AI_PROCESS_EVERY_N_FRAMES = 3  # Process 1 out of every N frames
AI_CONFIDENCE_THRESHOLD = 0.5  # YOLO detection confidence (0.0-1.0)

# WebSocket Configuration
CORS_ALLOWED_ORIGINS = "*"

# Logging Configuration
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"

# Auto Mode Configuration (Autonomous Fire Suppression)
AUTO_MODE_ENABLED = False  # Default: auto mode ALWAYS ON
AUTO_FRAME_WIDTH = 640  # ESP32-CAM resolution
AUTO_FRAME_HEIGHT = 480
AUTO_LEFT_THRESHOLD_RATIO = 0.33  # Fire ở 1/3 TRÁI → Quay trái
AUTO_RIGHT_THRESHOLD_RATIO = 0.67  # Fire ở 1/3 PHẢI → Quay phải
AUTO_FIRE_CLOSE_AREA = 50000  # Pixels² - stop and spray (e.g., 200x250 bbox)
AUTO_FIRE_MEDIUM_AREA = (
    25000  # Pixels² - slow approach + spray (INCREASED for earlier spray)
)
AUTO_SPEED_FORWARD = 175  # Motor speed forward (robot nặng - tốc độ vừa phải)
AUTO_SPEED_TURN = 225  # Turn speed (robot nặng - cần tốc độ CAO để quay)
AUTO_MIN_DISTANCE_CM = 25  # Stop if obstacle closer (INCREASED for safety)
AUTO_CONTROL_LOOP_HZ = 2  # Control loop frequency (2 Hz = 500ms update)
