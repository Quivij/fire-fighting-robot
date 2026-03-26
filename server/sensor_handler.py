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

            return {
                "distance": distance_data.get("distance", -1),
                "flame_digital": flame_data.get("digital", False),
            }

        except Exception as e:
            logger.error(f"[SensorHandler] Error: {e}")
            return {
                "distance": -1,
                "flame_digital": False,
            }