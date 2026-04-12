import time
import threading
import logging

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

        # ===== STATE =====
        self.has_seen_fire = False
        self.searching = False
        self.search_start = 0

        # ===== ANTI SPAM =====
        self.last_action = None
        self.last_speed = 0
        self.last_time = 0
        self.last_pump_state = None

    # =============================
    # MAIN LOOP
    # =============================
    def _loop(self):
        delay = 1.0 / AUTO_CONTROL_LOOP_HZ

        # 🛑 đảm bảo đứng yên khi start
        self._send_motor("stop", 0)
        self._pump("off")
        time.sleep(0.3)

        while not self._stop_event.is_set():
            try:
                cam = self.camera.get_fire_status() or {}
                sensor = self.sensor.get_all() or {}

                cam_detected = cam.get("detected", False)
                flame = sensor.get("detected", False)
                distance = sensor.get("distance", -1)

                print(f"[AUTO] cam={cam_detected}, flame={flame}, dist={distance}")

                # ===== TRÁNH VẬT CẢN =====
                if distance > 0 and distance < 15:
                    print("🚧 OBSTACLE")
                    self._send_motor("stop", 0)
                    self._pump("off")
                    time.sleep(delay)
                    continue

                # ===== CÓ LỬA =====
                if flame or cam_detected:
                    print("🔥 FIRE DETECTED")

                    self.has_seen_fire = True
                    self.searching = False

                    self._send_motor("forward", 40)
                    self._pump("on")

                # ===== KHÔNG CÓ LỬA =====
                else:
                    # chưa từng thấy → đứng yên
                    if not self.has_seen_fire:
                        self._send_motor("stop", 0)
                        self._pump("off")

                    # đã từng thấy → search
                    else:
                        self._handle_search()

                time.sleep(delay)

            except Exception as e:
                logger.error(f"[AUTO ERROR] {e}")
                time.sleep(1)

    # =============================
    # 🔍 SEARCH MODE
    # =============================
    def _handle_search(self):
        now = time.time()

        if not self.searching:
            print("🔍 START SEARCH")
            self.searching = True
            self.search_start = now

        elapsed = now - self.search_start

        # 👉 chỉ đi thẳng nhẹ
        if elapsed < 2.0:
            self._force_motor("forward", 20)  # tốc độ thấp cho ổn định

        # hết thời gian → dừng
        else:
            print("🛑 STOP SEARCH")
            self._send_motor("stop", 0)
            self._pump("off")
            self.searching = False
            self.has_seen_fire = False

    # =============================
    # 🚀 MOTOR
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

    # 🔥 FORCE (bỏ anti-spam cho search)
    def _force_motor(self, action, speed):
        payload = {
            "action": action,
            "speed": int(speed),
        }

        print(f"[FORCE] MOTOR: {payload}")
        self.mqtt.publish(TOPIC_MOTOR_CONTROL, payload)

        self.last_action = action
        self.last_speed = speed
        self.last_time = time.time()

    # =============================
    # 💦 PUMP
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