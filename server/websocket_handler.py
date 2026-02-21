"""
WebSocket Handler - Xử lý các sự kiện từ Frontend
Nhận lệnh từ client và chuyển tiếp qua MQTT tới ESP32
"""

from flask_socketio import emit
from config import TOPIC_MOTOR_CONTROL, TOPIC_PUMP_CONTROL
import logging

logger = logging.getLogger(__name__)

class WebSocketHandler:
    """
    Quản lý WebSocket events từ Frontend
    Validate dữ liệu và publish lệnh tới MQTT
    """

    def __init__(self, mqtt_handler, socketio_instance):
        """
        Khởi tạo WebSocket handler

        Args:
            mqtt_handler: Instance của MQTTHandler để publish messages
            socketio_instance: Flask-SocketIO instance để emit events
        """
        self.mqtt = mqtt_handler
        self.socketio = socketio_instance

        # Danh sách action hợp lệ
        self.VALID_MOTOR_ACTIONS = ['forward', 'backward', 'left', 'right', 'stop']
        self.VALID_PUMP_STATES = ['on', 'off', 'toggle']

        logger.info("[WebSocket] Handler initialized")

    def register_events(self):
        """
        Đăng ký tất cả WebSocket event handlers
        Gọi hàm này trong app.py sau khi khởi tạo
        """

        @self.socketio.on('connect')
        def handle_connect():
            """Client kết nối tới WebSocket server"""
            logger.info(f"[WebSocket] Client connected")

            # Gửi trạng thái robot hiện tại cho client mới
            if self.mqtt.robot_status:
                emit('robot_status', self.mqtt.robot_status)
                logger.debug(f"[WebSocket] Sent current status to new client")

        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Client ngắt kết nối"""
            logger.info(f"[WebSocket] Client disconnected")

        @self.socketio.on('motor_command')
        def handle_motor_command(data):
            """
            Xử lý lệnh điều khiển motor từ Frontend

            Expected data:
            {
                "action": "forward" | "backward" | "left" | "right" | "stop",
                "speed": 0-255 (optional, default 200)
            }
            """
            try:
                # Validate action
                action = data.get('action')
                if not action:
                    emit('error', {'message': 'Missing action field'})
                    logger.warning("[WebSocket] Motor command missing action")
                    return

                if action not in self.VALID_MOTOR_ACTIONS:
                    emit('error', {'message': f'Invalid action: {action}'})
                    logger.warning(f"[WebSocket] Invalid motor action: {action}")
                    return

                # Validate speed (optional, default 200)
                speed = data.get('speed', 200)
                if not isinstance(speed, int) or speed < 0 or speed > 255:
                    emit('error', {'message': 'Speed must be integer 0-255'})
                    logger.warning(f"[WebSocket] Invalid speed: {speed}")
                    return

                # Publish tới MQTT với QoS 0 (low latency)
                mqtt_payload = {
                    'action': action,
                    'speed': speed
                }

                success = self.mqtt.publish(TOPIC_MOTOR_CONTROL, mqtt_payload, qos=0)

                if success:
                    # Acknowledge thành công
                    emit('command_ack', {
                        'type': 'motor',
                        'action': action,
                        'speed': speed,
                        'status': 'sent'
                    })
                    logger.info(f"[WebSocket] Motor command sent: {action} @ {speed}")
                else:
                    emit('error', {'message': 'Failed to publish MQTT message'})
                    logger.error(f"[WebSocket] Failed to publish motor command")

            except Exception as e:
                emit('error', {'message': f'Server error: {str(e)}'})
                logger.error(f"[WebSocket] Error handling motor command: {e}")

        @self.socketio.on('pump_command')
        def handle_pump_command(data):
            """
            Xử lý lệnh điều khiển pump từ Frontend

            Expected data:
            {
                "state": "on" | "off" | "toggle"
            }
            """
            try:
                # Validate state
                state = data.get('state')
                if not state:
                    emit('error', {'message': 'Missing state field'})
                    logger.warning("[WebSocket] Pump command missing state")
                    return

                if state not in self.VALID_PUMP_STATES:
                    emit('error', {'message': f'Invalid state: {state}'})
                    logger.warning(f"[WebSocket] Invalid pump state: {state}")
                    return

                # Publish tới MQTT với QoS 0
                mqtt_payload = {
                    'state': state
                }

                success = self.mqtt.publish(TOPIC_PUMP_CONTROL, mqtt_payload, qos=0)

                if success:
                    # Acknowledge thành công
                    emit('command_ack', {
                        'type': 'pump',
                        'state': state,
                        'status': 'sent'
                    })
                    logger.info(f"[WebSocket] Pump command sent: {state}")
                else:
                    emit('error', {'message': 'Failed to publish MQTT message'})
                    logger.error(f"[WebSocket] Failed to publish pump command")

            except Exception as e:
                emit('error', {'message': f'Server error: {str(e)}'})
                logger.error(f"[WebSocket] Error handling pump command: {e}")

        logger.info("[WebSocket] All event handlers registered")