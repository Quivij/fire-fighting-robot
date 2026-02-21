import logging
from datetime import datetime

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO

from auto_controller import AutoController
from camera_handler import CameraHandler

# Import các module đã tạo
from config import (
    ESP32_CAM_URL,
    FLASK_HOST,
    FLASK_PORT,
    MQTT_BROKER,
    MQTT_PORT,
    SECRET_KEY,
)
from mqtt_handler import MQTTHandler
from websocket_handler import WebSocketHandler

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Khởi tạo Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY

# Cấu hình CORS (cho phép Frontend kết nối)
CORS(
    app,
    resources={
        r"/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type"],
        }
    },
)

# Khởi tạo SocketIO với cấu hình CORS
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# Khởi tạo MQTT Handler
mqtt_handler = MQTTHandler(socketio)

# Khởi tạo WebSocket Handler
ws_handler = WebSocketHandler(mqtt_handler, socketio)

# Đăng ký WebSocket events
ws_handler.register_events()

# Khởi tạo Camera Handler
camera_handler = CameraHandler(ESP32_CAM_URL)

# Khởi tạo Auto Controller
auto_controller = AutoController(mqtt_handler, camera_handler)


# ===== REST API ENDPOINTS =====


@app.route("/")
def index():
    """Root endpoint - Health check"""
    return jsonify(
        {
            "service": "Fire Fighting Robot Backend",
            "status": "running",
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "ok",
            "mqtt_connected": mqtt_handler.connected,
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/status")
def get_status():
    """
    GET /api/status
    Lấy trạng thái hiện tại của robot
    """
    return jsonify(
        {
            "status": "ok",
            "mqtt_connected": mqtt_handler.connected,
            "robot_status": mqtt_handler.robot_status,
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/motor", methods=["POST"])
def control_motor():
    """
    POST /api/motor
    Điều khiển motor qua REST API (alternative to WebSocket)

    Body:
    {
        "action": "forward" | "backward" | "left" | "right" | "stop",
        "speed": 0-255 (optional, default 200)
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        action = data.get("action")
        speed = data.get("speed", 200)

        # Validate action
        valid_actions = ["forward", "backward", "left", "right", "stop"]
        if action not in valid_actions:
            return jsonify({"error": f"Invalid action: {action}"}), 400

        # Validate speed
        if not isinstance(speed, int) or speed < 0 or speed > 255:
            return jsonify({"error": "Speed must be integer 0-255"}), 400

        # Publish tới MQTT
        from config import TOPIC_MOTOR_CONTROL

        mqtt_payload = {"action": action, "speed": speed}

        success = mqtt_handler.publish(TOPIC_MOTOR_CONTROL, mqtt_payload, qos=0)

        if success:
            logger.info(f"[API] Motor command: {action} @ {speed}")
            return jsonify(
                {
                    "status": "ok",
                    "action": action,
                    "speed": speed,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        else:
            return jsonify({"error": "Failed to publish MQTT message"}), 500

    except Exception as e:
        logger.error(f"[API] Error controlling motor: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/pump", methods=["POST"])
def control_pump():
    """
    POST /api/pump
    Điều khiển pump qua REST API

    Body:
    {
        "state": "on" | "off" | "toggle"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        state = data.get("state")

        # Validate state
        valid_states = ["on", "off", "toggle"]
        if state not in valid_states:
            return jsonify({"error": f"Invalid state: {state}"}), 400

        # Publish tới MQTT
        from config import TOPIC_PUMP_CONTROL

        mqtt_payload = {"state": state}

        success = mqtt_handler.publish(TOPIC_PUMP_CONTROL, mqtt_payload, qos=0)

        if success:
            logger.info(f"[API] Pump command: {state}")
            return jsonify(
                {
                    "status": "ok",
                    "state": state,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        else:
            return jsonify({"error": "Failed to publish MQTT message"}), 500

    except Exception as e:
        logger.error(f"[API] Error controlling pump: {e}")
        return jsonify({"error": str(e)}), 500


# ===== CAMERA ENDPOINTS =====


@app.route("/api/camera/stream/original")
def camera_stream_original():
    """
    GET /api/camera/stream/original
    Original camera stream from ESP32-CAM (no AI processing)

    Returns:
        MJPEG stream (multipart/x-mixed-replace)
    """
    return Response(
        camera_handler.generate_original_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/camera/stream/processed")
def camera_stream_processed():
    """
    GET /api/camera/stream/processed
    AI-processed camera stream (future: with fire detection bounding boxes)

    Returns:
        MJPEG stream (multipart/x-mixed-replace)
    """
    return Response(
        camera_handler.generate_processed_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/camera/status")
def camera_status():
    """
    GET /api/camera/status
    Get camera status from ESP32-CAM

    Returns:
        JSON with camera status
    """
    status = camera_handler.get_camera_status()
    return jsonify(status)


@app.route("/api/fire/status")
def fire_status():
    """
    GET /api/fire/status
    Get fire detection status

    Returns:
        JSON with fire detection status
    """
    fire_status = camera_handler.get_fire_status()

    return jsonify({"detection": fire_status})


# ===== AUTO MODE ENDPOINTS =====


@app.route("/api/auto/enable", methods=["POST"])
def enable_auto_mode():
    """
    POST /api/auto/enable
    Bật chế độ tự động - robot sẽ tự động tìm và dập lửa

    Returns:
        JSON with success status
    """
    try:
        success = auto_controller.enable_auto_mode()

        if success:
            logger.warning("[API] 🤖 AUTO MODE ENABLED by user")
            return jsonify(
                {
                    "status": "ok",
                    "auto_mode": True,
                    "message": "Autonomous fire suppression enabled",
                }
            )
        else:
            return jsonify(
                {"status": "error", "message": "Auto mode already enabled"}
            ), 400

    except Exception as e:
        logger.error(f"[API] Error enabling auto mode: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/auto/disable", methods=["POST"])
def disable_auto_mode():
    """
    POST /api/auto/disable
    Tắt chế độ tự động - chuyển về manual control

    Returns:
        JSON with success status
    """
    try:
        success = auto_controller.disable_auto_mode()

        if success:
            logger.info("[API] AUTO MODE DISABLED by user")
            return jsonify(
                {
                    "status": "ok",
                    "auto_mode": False,
                    "message": "Manual control restored",
                }
            )
        else:
            return jsonify(
                {"status": "error", "message": "Auto mode already disabled"}
            ), 400

    except Exception as e:
        logger.error(f"[API] Error disabling auto mode: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/auto/status")
def auto_status():
    """
    GET /api/auto/status
    Lấy trạng thái auto mode

    Returns:
        JSON with auto mode status
    """
    try:
        status = auto_controller.get_status()
        return jsonify(
            {
                "status": "ok",
                "auto_controller": status,
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"[API] Error getting auto status: {e}")
        return jsonify({"error": str(e)}), 500


# ===== APPLICATION STARTUP =====

if __name__ == "__main__":
    # Banner
    print("\n" + "=" * 60)
    print("  FIRE FIGHTING ROBOT - BACKEND SERVER")
    print("=" * 60)
    print(f"  Flask Server:   http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"  MQTT Broker:    {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  WebSocket:      ws://{FLASK_HOST}:{FLASK_PORT}")
    print("=" * 60 + "\n")

    # Kết nối MQTT trước khi start server
    logger.info("[MQTT] Connecting to broker...")
    mqtt_handler.connect()

    # Start Camera Handler
    logger.info("[Camera] Starting camera stream handler...")
    camera_handler.start()

    # Auto-start AUTO MODE
    from config import AUTO_MODE_ENABLED

    if AUTO_MODE_ENABLED:
        logger.warning("[AutoController] AUTO MODE enabled by default")
        auto_controller.enable_auto_mode()

    # Start Flask-SocketIO server
    logger.info("[Flask] Starting server...")
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=True,
        use_reloader=False,  # Tắt reloader để tránh duplicate MQTT connections
    )
