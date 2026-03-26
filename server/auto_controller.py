import time
import threading
import logging
import random

from config import (
    TOPIC_MOTOR_CONTROL,
    TOPIC_PUMP_CONTROL,
    AUTO_CONTROL_LOOP_HZ,
    AUTO_SPEED_FORWARD,
    AUTO_SPEED_TURN,
)

logger = logging.getLogger(__name__)


class AutoController:
    def __init__(self, mqtt_handler, camera_handler, sensor_handler):
        self.mqtt = mqtt_handler
        self.camera = camera_handler
        self.sensor = sensor_handler

        self.auto_mode = False
        self._thread = None
        self._stop_event = threading.Event()

        self.state = "SEARCH"

        # debounce
        self.fire_count = 0
        self.flame_count = 0
        self.no_fire_count = 0

        # anti spam
        self.last_action = None
        self.last_speed = 0
        self.last_time = 0

    # =============================
    # LOOP
    # =============================
    def _loop(self):
        delay = 1.0 / AUTO_CONTROL_LOOP_HZ

        while not self._stop_event.is_set():
            try:
                cam = self.camera.get_fire_status()
                sensor = self.sensor.get_all()

                cam_detected = cam.get("detected", False)
                flame = sensor.get("flame_digital", False)
                distance = sensor.get("distance", -1)

                print(f"[DEBUG] fire={cam_detected}, flame={flame}, dist={distance}, state={self.state}")

                # ===== FILTER =====
                self.fire_count = self.fire_count + 1 if cam_detected else 0
                self.flame_count = self.flame_count + 1 if flame else 0

                fire_ok = self.fire_count >= 3
                flame_ok = self.flame_count >= 2

                # =============================
                # 🚧 OBSTACLE (ưu tiên cao nhất)
                # =============================
                if 0 < distance < 25:
                    self._handle_obstacle()

                # =============================
                # 🔥 EXTINGUISH (rất gần)
                # =============================
                elif flame_ok:
                    self._handle_extinguish(distance)

                # =============================
                # 🎯 TRACK FIRE
                # =============================
                elif fire_ok:
                    self.no_fire_count = 0
                    self._handle_tracking(cam, distance)

                # =============================
                # 🔍 SEARCH
                # =============================
                else:
                    self.no_fire_count += 1
                    self._handle_search()

                time.sleep(delay)

            except Exception as e:
                logger.error(f"[AUTO ERROR] {e}")
                time.sleep(1)

    # =============================
    # SPEED CONTROL
    # =============================
    def _get_speed_by_distance(self, distance):
        if distance <= 0:
            return AUTO_SPEED_FORWARD

        if distance < 20:
            return 0
        elif distance < 30:
            return 70
        elif distance < 60:
            return 110
        else:
            return AUTO_SPEED_FORWARD

    # =============================
    # 🚧 OBSTACLE
    # =============================
    def _handle_obstacle(self):
        if self.state != "AVOID":
            logger.warning("🚧 OBSTACLE → AVOID")
            self.state = "AVOID"

        # dừng mạnh
        self._send_motor("stop", 0)
        time.sleep(0.3)

        # lùi ra
        self._send_motor("backward", 100)
        time.sleep(0.3)

        # quay
        direction = random.choice(["left", "right"])
        self._send_motor(direction, AUTO_SPEED_TURN)
        time.sleep(0.4)

    # =============================
    # 🔥 EXTINGUISH
    # =============================
    def _handle_extinguish(self, distance):
        if self.state != "EXTINGUISH":
            logger.warning("🔥 EXTINGUISH")
            self.state = "EXTINGUISH"

        # gần → dừng hẳn
        if distance < 20:
            self._send_motor("stop", 0)
        else:
            self._send_motor("forward", 80)

        self._pump("on")

    # =============================
    # 🎯 TRACK
    # =============================
    def _handle_tracking(self, fire, distance):
        x = fire.get("x_center")
        w = fire.get("frame_width")

        if not x or not w:
            return

        if self.state != "TRACK":
            logger.info("🎯 TRACK")
            self.state = "TRACK"

        offset = (x - w / 2) / (w / 2)
        speed = self._get_speed_by_distance(distance)

        self._pump("off")

        # ⭐ FIX QUAN TRỌNG: dừng thật
        if speed == 0:
            self._send_motor("stop", 0)
            return

        if abs(offset) < 0.2:
            self._send_motor("forward", speed)
        elif offset < 0:
            self._send_motor("left", AUTO_SPEED_TURN)
        else:
            self._send_motor("right", AUTO_SPEED_TURN)

    # =============================
    # 🔍 SEARCH
    # =============================
    def _handle_search(self):
        if self.state != "SEARCH":
            logger.info("🔍 SEARCH (FORWARD SLOW)")
            self.state = "SEARCH"

            self._send_motor("forward", 60)  # chạy chậm
            self._pump("off")

    # =============================
    # MQTT
    # =============================
    def _send_motor(self, action, speed):
        now = time.time()

        # chống spam
        if (
            action == self.last_action
            and abs(speed - self.last_speed) < 5
            and now - self.last_time < 0.2
        ):
            return

        payload = {
            "action": action,
            "speed": int(speed),
        }

        print(f"[SEND] MOTOR: {payload}")
        self.mqtt.publish(TOPIC_MOTOR_CONTROL, payload)

        self.last_action = action
        self.last_speed = speed
        self.last_time = now

    def _pump(self, state):
        payload = {"state": state}
        print(f"[SEND] PUMP: {payload}")
        self.mqtt.publish(TOPIC_PUMP_CONTROL, payload)

    # =============================
    # CONTROL
    # =============================
    def enable_auto_mode(self):
        if self.auto_mode:
            return False

        logger.warning("🤖 AUTO MODE ENABLED")

        self.auto_mode = True
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._loop)
        self._thread.start()

        return True

    def disable_auto_mode(self):
        if not self.auto_mode:
            return False

        logger.info("🛑 AUTO MODE DISABLED")

        self.auto_mode = False
        self._stop_event.set()

        return True

    def get_status(self):
        return {
            "auto_mode": self.auto_mode,
            "state": self.state
        }