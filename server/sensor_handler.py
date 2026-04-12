import logging

logger = logging.getLogger(__name__)


class SensorHandler:
    def __init__(self, mqtt_handler):
        self.mqtt = mqtt_handler
        logger.info("[SensorHandler] Initialized (using MQTT cache)")

    # =============================
    # PUBLIC API
    # =============================
    def get_all(self):
        try:
            # Lấy data từ MQTTHandler
            distance_data = self.mqtt.sensor_distance or {}
            flame_data = self.mqtt.sensor_flame or {}

            # 🔥 FIX QUAN TRỌNG: đúng key từ ESP32
            detected = flame_data.get("detected", False)

            # Debug để kiểm tra
            print("🔥 SENSOR_HANDLER READ:", detected)

            return {
                "distance": distance_data.get("distance", -1),
                "detected": detected,   # 🔥 AutoController cần key này
            }

        except Exception as e:
            logger.error(f"[SensorHandler] Error: {e}")
            return {
                "distance": -1,
                "detected": False,
            }