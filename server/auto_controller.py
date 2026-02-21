import time
import threading
import logging

from config import (
    TOPIC_MOTOR_CONTROL,
    TOPIC_PUMP_CONTROL,
    AUTO_CONTROL_LOOP_HZ,
    AUTO_SPEED_FORWARD,
    AUTO_SPEED_TURN,
)

logger = logging.getLogger(__name__)


class AutoController:
    """
    AutoController
    =================
    - Quay tìm lửa khi chưa phát hiện
    - Khi xác nhận có lửa:
        + Dừng robot
        + Bật bơm
    """

    def __init__(self, mqtt_handler, camera_handler):
        self.mqtt = mqtt_handler
        self.camera = camera_handler

        self.auto_mode = False
        self._thread = None
        self._stop_event = threading.Event()

        # -------- FILTER / DEBOUNCE --------
        self.fire_counter = 0
        self.FIRE_CONFIRM_COUNT = 3  # số lần liên tiếp cần thấy lửa

    # =================================================
    # PUBLIC METHODS
    # =================================================

    def enable_auto_mode(self):
        if self.auto_mode:
            logger.warning("[AutoController] Auto mode already enabled")
            return False

        self.auto_mode = True
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._auto_loop, daemon=True
        )
        self._thread.start()

        logger.warning("[AutoController] 🤖 AUTO MODE ENABLED")
        return True

    def disable_auto_mode(self):
        if not self.auto_mode:
            logger.warning("[AutoController] Auto mode already disabled")
            return False

        self.auto_mode = False
        self._stop_event.set()

        # an toàn
        self._stop_robot()
        self._pump_off()

        logger.info("[AutoController] AUTO MODE DISABLED")
        return True

    def get_status(self):
        return {
            "auto_mode": self.auto_mode,
            "thread_alive": self._thread.is_alive()
            if self._thread
            else False,
            "loop_hz": AUTO_CONTROL_LOOP_HZ,
        }

    # =================================================
    # AUTO LOOP
    # =================================================

    def _auto_loop(self):
        logger.info("[AutoController] Auto loop started")
        loop_delay = 1.0 / AUTO_CONTROL_LOOP_HZ

        while not self._stop_event.is_set():
            try:
                fire_status = self.camera.get_fire_status()

                # -------- DEBOUNCE --------
                if fire_status.get("detected"):
                    self.fire_counter += 1
                else:
                    self.fire_counter = 0

                fire_confirmed = (
                    self.fire_counter >= self.FIRE_CONFIRM_COUNT
                )

                # -------- LOGIC --------
                if fire_confirmed:
                    logger.warning("🔥 FIRE CONFIRMED")

                    self._stop_robot()
                    self._pump_on()
                else:
                    self._search_fire()
                    self._pump_off()

                time.sleep(loop_delay)

            except Exception as e:
                logger.error(f"[AutoController] Error: {e}")
                time.sleep(1)

        logger.info("[AutoController] Auto loop stopped")

    # =================================================
    # MOVEMENT
    # =================================================

    def _move_forward(self, speed):
        self._publish_motor("forward", speed)

    def _turn_left(self, speed):
        self._publish_motor("left", speed)

    def _stop_robot(self):
        self._publish_motor("stop", 0)

    def _search_fire(self):
        logger.info("[AutoController] Searching for fire...")
        self._turn_left(AUTO_SPEED_TURN)

    # =================================================
    # PUMP
    # =================================================

    def _pump_on(self):
        self._publish_pump("on")

    def _pump_off(self):
        self._publish_pump("off")

    # =================================================
    # MQTT HELPERS
    # =================================================

    def _publish_motor(self, action, speed):
        payload = {
            "action": action,
            "speed": speed,
        }
        self.mqtt.publish(TOPIC_MOTOR_CONTROL, payload)

    def _publish_pump(self, state):
        payload = {"state": state}
        self.mqtt.publish(TOPIC_PUMP_CONTROL, payload)
