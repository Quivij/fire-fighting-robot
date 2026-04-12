import time
import threading
import logging
import random

from config import (
    TOPIC_MOTOR_CONTROL,
    TOPIC_PUMP_CONTROL,
    AUTO_CONTROL_LOOP_HZ,
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
        self.state_time = time.time()

        # debounce
        self.fire_count = 0
        self.flame_count = 0

        # anti spam motor
        self.last_action = None
        self.last_speed = 0
        self.last_time = 0

        # anti spam pump
        self.last_pump_state = None

    # =============================
    # MAIN LOOP
    # =============================
    def _loop(self):
        delay = 1.0 / AUTO_CONTROL_LOOP_HZ

        while not self._stop_event.is_set():
            try:
                cam = self.camera.get_fire_status()
                sensor = self.sensor.get_all()

                # ===== DATA =====
                cam_detected = cam.get("detected", False)
                flame = sensor.get("detected", False)  # ✅ FIX
                distance = sensor.get("distance", -1)

                print(f"[AUTO] fire={cam_detected}, flame={flame}, dist={distance}, state={self.state}")

                # ===== FILTER =====
                self.fire_count = self.fire_count + 1 if cam_detected else 0
                self.flame_count = self.flame_count + 1 if flame else 0

                fire_ok = self.fire_count >= 3
                flame_ok = self.flame_count >= 2

                # ===== PRIORITY =====
                if flame_ok:
                    self._handle_extinguish(distance)

                elif 0 < distance < 50:
                    self._handle_obstacle()

                elif fire_ok:
                    self._handle_tracking(cam, distance)

                else:
                    self._handle_search()

                time.sleep(delay)

            except Exception as e:
                logger.error(f"[AUTO ERROR] {e}")
                time.sleep(1)

    # =============================
    # 🔥 EXTINGUISH
    # =============================
    def _handle_extinguish(self, distance):
        if self.state != "EXTINGUISH":
            logger.warning("🔥 EXTINGUISH")
            self.state = "EXTINGUISH"

        SAFE_DISTANCE = 40

        if distance <= 0:
            self._send_motor("forward", 30)

        elif distance < SAFE_DISTANCE:
            self._send_motor("stop", 0)

        else:
            self._send_motor("forward", 40)

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

        if speed == 0:
            self._send_motor("stop", 0)
            return

        if abs(offset) < 0.15:
            self._send_motor("forward", speed)
        elif offset < 0:
            self._send_motor("left", speed)
        else:
            self._send_motor("right", speed)

    # =============================
    # 🚧 OBSTACLE
    # =============================
    def _handle_obstacle(self):
        now = time.time()

        if self.state != "AVOID":
            logger.warning("🚧 OBSTACLE")
            self.state = "AVOID"
            self.state_time = now

        elapsed = now - self.state_time

        if elapsed < 0.5:
            self._send_motor("stop", 0)

        elif elapsed < 1.2:
            self._send_motor("backward", 40)

        elif elapsed < 2.0:
            self._send_motor(random.choice(["left", "right"]), 60)

        else:
            self.state = "SEARCH"

    # =============================
    # 🔍 SEARCH
    # =============================
    def _handle_search(self):
        if self.state != "SEARCH":
            logger.info("🔍 SEARCH")
            self.state = "SEARCH"

        self._send_motor("forward", 50)
        self._pump("off")

    # =============================
    # ⚡ SPEED CONTROL
    # =============================
    def _get_speed_by_distance(self, distance):
        if distance <= 0:
            return 80

        if distance < 30:
            return 0
        elif distance < 50:
            return 20
        elif distance < 80:
            return 40
        else:
            return 80

    # =============================
    # 🚀 MOTOR (ANTI SPAM)
    # =============================
    def _send_motor(self, action, speed):
        now = time.time()

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

    # =============================
    # 💦 PUMP (ANTI SPAM)
    # =============================
    def _pump(self, state):
        if state == self.last_pump_state:
            return

        payload = {"state": state}

        print(f"[SEND] PUMP: {payload}")
        self.mqtt.publish(TOPIC_PUMP_CONTROL, payload)

        self.last_pump_state = state

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
            "state": self.state,
        }