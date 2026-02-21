"""
MQTT Handler Module
Manages MQTT client connection and message handling
"""

import json
import logging

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER,
    MQTT_CLIENT_ID,
    MQTT_KEEPALIVE,
    MQTT_PORT,
    TOPIC_SENSOR_DISTANCE,
    TOPIC_SENSOR_FLAME,
    TOPIC_STATUS,
)

logger = logging.getLogger(__name__)


class MQTTHandler:
    """
    MQTT Client Handler
    Quản lý kết nối tới MQTT broker và xử lý messages từ ESP32
    """

    def __init__(self, socketio_instance):
        """
        Initialize MQTT Handler

        Args:
            socketio_instance: Flask-SocketIO instance để broadcast messages
        """
        self.client = mqtt.Client(MQTT_CLIENT_ID)
        self.socketio = socketio_instance
        self.connected = False
        self.robot_status = {}
        self.sensor_distance = {}
        self.sensor_flame = {}

        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        """
        Callback khi kết nối tới MQTT broker
        """
        if rc == 0:
            self.connected = True
            logger.info(f"MQTT connected to {MQTT_BROKER}:{MQTT_PORT}")

            # Subscribe to all robot topics
            client.subscribe(TOPIC_STATUS)
            client.subscribe(TOPIC_SENSOR_DISTANCE)
            client.subscribe(TOPIC_SENSOR_FLAME)

            logger.info(f"Subscribed to topics:")
            logger.info(f"  - {TOPIC_STATUS}")
            logger.info(f"  - {TOPIC_SENSOR_DISTANCE}")
            logger.info(f"  - {TOPIC_SENSOR_FLAME}")
        else:
            self.connected = False
            logger.error(f"MQTT connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """
        Callback khi mất kết nối MQTT
        """
        self.connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly")
        else:
            logger.info("MQTT disconnected")

    def _on_message(self, client, userdata, msg):
        """
        Callback khi nhận message từ MQTT
        Xử lý messages từ ESP32 và broadcast lên frontend qua WebSocket
        """
        try:
            # Decode JSON payload
            payload = json.loads(msg.payload.decode())
            topic = msg.topic

            # Route message based on topic
            if topic == TOPIC_STATUS:
                # Robot status update
                self.robot_status = payload
                self.socketio.emit("robot_status", payload)
                logger.debug(
                    f"[Status] motor={payload.get('motor')}, pump={payload.get('pump')}"
                )

            elif topic == TOPIC_SENSOR_DISTANCE:
                # Distance sensor data
                self.sensor_distance = payload
                self.socketio.emit("sensor_distance", payload)
                logger.debug(
                    f"[Distance] {payload.get('distance')} {payload.get('unit')}"
                )

            elif topic == TOPIC_SENSOR_FLAME:
                # Flame sensor data
                self.sensor_flame = payload
                self.socketio.emit("sensor_flame", payload)
                logger.debug(
                    f"[Flame] digital={payload.get('digital')}, analog={payload.get('analog')}"
                )

            else:
                logger.warning(f"Unknown topic: {topic}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from topic {msg.topic}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def connect(self):
        """
        Kết nối tới MQTT broker
        """
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
            self.client.loop_start()
            logger.info("MQTT client loop started")
        except Exception as e:
            logger.error(f"Failed to connect MQTT: {e}")
            raise

    def publish(self, topic, payload, qos=0):
        """
        Publish message tới MQTT topic

        Args:
            topic: MQTT topic
            payload: Message payload (dict hoặc str)
            qos: Quality of Service (0, 1, 2)

        Returns:
            bool: True nếu publish thành công
        """
        if not self.connected:
            logger.error("Cannot publish: MQTT not connected")
            return False

        try:
            # Convert dict to JSON string
            if isinstance(payload, dict):
                payload = json.dumps(payload)

            result = self.client.publish(topic, payload, qos=qos)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {topic}: {payload}")
                return True
            else:
                logger.error(f"Failed to publish to {topic}")
                return False

        except Exception as e:
            logger.error(f"Error publishing to {topic}: {e}")
            return False

    def disconnect(self):
        """
        Ngắt kết nối MQTT
        """
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT client disconnected")
