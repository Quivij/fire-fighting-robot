"""
Fire Detection Module
Detects DYNAMIC fire (flickering) vs STATIC images
Uses color detection + frame differencing to distinguish real fire
"""

import logging
import time
from collections import deque
from threading import Lock

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FireDetector:
    """
    Fire Detection with Dynamic vs Static discrimination

    Real fire characteristics:
    - Color: Red/Orange/Yellow (HSV color space)
    - Motion: Continuous pixel changes (flickering)
    - Turbulence: Frame differences in fire regions

    Static images:
    - Color: May have fire colors
    - Motion: NO pixel changes frame-to-frame
    """

    def __init__(
        self,
        model_path=None,
        confidence_threshold=0.6,
        enable_motion_check=True,
        motion_history_size=5,
    ):
        """
        Initialize Fire Detector
        """

        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.enable_motion_check = enable_motion_check
        self.detection_lock = Lock()

        # Detection state
        self.fire_detected = False
        self.dynamic_fire_detected = False
        self.static_fire_detected = False
        self.last_detection_time = 0
        self.detection_count = 0

        # Motion settings
        self.motion_history_size = motion_history_size
        self.motion_history = deque(maxlen=motion_history_size)
        self.prev_frames = deque(maxlen=3)

        # ✅ THÊM DÒNG NÀY (FIX LỖI)
        self.motion_pixel_threshold = 25

        # Tunable thresholds
        self.min_motion_pixels_ratio = 0.08
        self.min_motion_score = 0.5
        self.min_turbulence_variance = 30   # ✅ giữ 1 dòng thôi

        # YOLO model
        self.model = None

        if model_path:
            self._load_yolo_model()
        else:
            logger.info("[FireDetector] Using color-based detection (no YOLO model)")

        if enable_motion_check:
            logger.info(
                f"[FireDetector] Motion detection ENABLED "
                f"(history={motion_history_size}, threshold={self.motion_pixel_threshold})"
            )
    def _load_yolo_model(self):
        try:
            from ultralytics import YOLO

            self.model = YOLO(self.model_path)
            logger.info(f"[FireDetector] YOLOv8 model loaded: {self.model_path}")

        except Exception as e:
            logger.error(f"[FireDetector] Failed to load YOLOv8 model: {e}")
            self.model = None
    def detect_fire(self, frame):
        if frame is None:
            return None, False, []

        try:
            processed = frame.copy()
            detections = []
            fire_detected = False

            # =============================
            # YOLO + COLOR COMBINE
            # =============================

            yolo_fire = False

            # ===== YOLO =====
            if self.model:
                results = self.model(frame, conf=0.3)

                if len(results) > 0 and results[0].boxes is not None:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    confs = results[0].boxes.conf.cpu().numpy()
                    classes = results[0].boxes.cls.cpu().numpy()

                    for i in range(len(boxes)):
                        x1, y1, x2, y2 = boxes[i]
                        conf = confs[i]
                        cls = int(classes[i])

                        name = self.model.names[cls].lower()

                        if name != "fire":
                            continue

                        yolo_fire = True
                        fire_detected = True

                        cx = int((x1 + x2) / 2)

                        cv2.rectangle(processed, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
                        cv2.putText(processed, f"YOLO {conf:.2f}",
                                    (int(x1), int(y1) - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                        detections.append({
                            "source": "yolo",
                            "x_center": cx,
                            "confidence": float(conf)
                        })

            # ===== COLOR (fallback) =====
            if not yolo_fire:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

                lower = np.array([0, 80, 80])
                upper = np.array([40, 255, 255])

                mask = cv2.inRange(hsv, lower, upper)

                kernel = np.ones((5, 5), np.uint8)
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                for cnt in contours:
                    area = cv2.contourArea(cnt)

                    if area < 300:
                        continue

                    x, y, w, h = cv2.boundingRect(cnt)
                    cx = x + w // 2

                    fire_detected = True

                    cv2.rectangle(processed, (x, y), (x+w, y+h), (0, 255, 255), 2)
                    cv2.putText(processed, "COLOR FIRE",
                                (x, y-10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 2)

                    detections.append({
                        "source": "color",
                        "x_center": cx,
                        "area": int(area)
                    })

            # ===== HOLD FIRE (QUAN TRỌNG) =====
            if fire_detected:
                self.last_detection_time = time.time()

            fire_still = (time.time() - self.last_detection_time) < 1.5

            return processed, fire_still, detections

        except Exception as e:
            logger.error(f"[FireDetector] Detect error: {e}")
            return frame, False, []
    def _detect_yolo(self, frame):
        try:
            frame_processed = self._preprocess_frame(frame)

            gray = cv2.cvtColor(frame_processed, cv2.COLOR_BGR2GRAY)
            self.prev_frames.append(gray)

            results = self.model(frame_processed, conf=self.confidence_threshold)

            detections = []
            dynamic_fire_detected = False
            processed_frame = frame_processed.copy()

            if len(results) > 0:
                result = results[0]

                if result.boxes is not None:
                    boxes = result.boxes.xyxy.cpu().numpy()
                    confidences = result.boxes.conf.cpu().numpy()
                    class_ids = result.boxes.cls.cpu().numpy()

                    for i in range(len(boxes)):
                        x1, y1, x2, y2 = boxes[i]
                        confidence = confidences[i]
                        class_id = int(class_ids[i])
                        class_name = self.model.names[class_id].lower()

                        # ✅ FIX 1: bỏ no_fire
                        # if class_name == "no_fire":
                        #     continue

                        # # ✅ FIX 2: chỉ lấy fire
                        # if class_name != "fire":
                        #     continue
                        # chỉ lấy class fire và confidence đủ cao
                        if class_name != "fire" or confidence < self.confidence_threshold:
                            continue

                        # === MOTION CHECK ===
                        detection_mask = np.zeros(gray.shape, dtype=np.uint8)
                        detection_mask[int(y1):int(y2), int(x1):int(x2)] = 255

                        motion_info = {"status": "disabled"}
                        if self.enable_motion_check:
                            motion_info = self._analyze_motion_in_region(detection_mask)

                        is_dynamic = motion_info.get("has_motion", False) or \
                                    motion_info.get("has_turbulence", False)

                        if is_dynamic:
                            dynamic_fire_detected = True
                            color = (0, 0, 255)
                            label = f"🔥 FIRE {confidence:.2f}"
                        else:
                            color = (128, 128, 128)
                            label = f"STATIC FIRE {confidence:.2f}"

                        # DRAW BOX
                        cv2.rectangle(
                            processed_frame,
                            (int(x1), int(y1)),
                            (int(x2), int(y2)),
                            color,
                            2
                        )

                        cv2.putText(
                            processed_frame,
                            label,
                            (int(x1), int(y1) - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            color,
                            2
                        )

                        detections.append({
                            "class": class_name,
                            "confidence": float(confidence),
                            "bbox": [int(x1), int(y1), int(x2), int(y2)],
                            "motion": motion_info,
                            "dynamic": is_dynamic
                        })

            return processed_frame, dynamic_fire_detected, detections

        except Exception as e:
            logger.error(f"[FireDetector] YOLO detection error: {e}")
            return frame, False, []
    def _analyze_motion_in_region(self, fire_mask):
        """
        Analyze motion/turbulence in fire regions across multiple frames

        Real fire has:
        1. Continuous pixel changes (high motion score)
        2. Turbulent patterns (variance in motion)
        3. Consistent flickering (motion in most frames)

        Args:
            fire_mask: Binary mask of detected fire regions

        Returns:
            dict: Motion analysis results
        """
        if len(self.prev_frames) < 2:
            # Need at least 2 frames to compare
            return {
                "has_motion": False,
                "motion_score": 0.0,
                "motion_pixels": 0,
                "motion_ratio": 0.0,
                "turbulence_variance": 0.0,
                "status": "insufficient_frames",
            }

        try:
            # Get current and previous grayscale frames
            current_gray = self.prev_frames[-1]
            prev_gray = self.prev_frames[-2]

            # Calculate frame difference
            frame_diff = cv2.absdiff(current_gray, prev_gray)

            # Apply fire mask to only analyze fire regions
            masked_diff = cv2.bitwise_and(frame_diff, frame_diff, mask=fire_mask)

            # Count pixels with significant change
            # motion_pixels = np.sum(masked_diff > self.motion_pixel_threshold)
            motion_pixels = np.sum(masked_diff > 25)  # tăng threshold

            # Calculate motion ratio (percentage of fire region that changed)
            fire_region_pixels = np.sum(fire_mask > 0)
            motion_ratio = (
                motion_pixels / fire_region_pixels if fire_region_pixels > 0 else 0
            )

            # Calculate motion score (0-1)
            motion_score = min(motion_ratio / self.min_motion_pixels_ratio, 1.0)

            # Add to motion history
            self.motion_history.append(motion_score)

            # Calculate turbulence (variance in motion across frames)
            turbulence_variance = (
                np.var(self.motion_history) if len(self.motion_history) > 2 else 0
            )

            # Calculate average motion score across history
            avg_motion_score = (
                np.mean(self.motion_history) if len(self.motion_history) > 0 else 0
            )

            # Determine if this is dynamic fire (flickering)
            has_motion = (
                avg_motion_score >= self.min_motion_score
                and motion_ratio >= self.min_motion_pixels_ratio
            )

            # Check for turbulence (fire should have varying motion, not constant)
            has_turbulence = turbulence_variance >= self.min_turbulence_variance

            return {
                "has_motion": has_motion,
                "has_turbulence": has_turbulence,
                "motion_score": motion_score,
                "avg_motion_score": avg_motion_score,
                "motion_pixels": int(motion_pixels),
                "motion_ratio": motion_ratio,
                "turbulence_variance": turbulence_variance,
                "fire_region_pixels": int(fire_region_pixels),
                "status": "dynamic_fire"
                if (has_motion or has_turbulence)
                else "static_image",
            }

        except Exception as e:
            logger.error(f"[FireDetector] Motion analysis error: {e}")
            return {
                "has_motion": False,
                "motion_score": 0.0,
                "motion_pixels": 0,
                "motion_ratio": 0.0,
                "turbulence_variance": 0.0,
                "status": "error",
            }

    def _preprocess_frame(self, frame):
        """
        Preprocess frame to fix ESP32-CAM overexposure issues

        Problems from ESP32-CAM:
        - Auto-exposure too high → Bright regions blown out (white)
        - Yellow flames → White (S<80) → Rejected by saturation filter
        - Color information lost in overexposed areas

        Solutions:
        1. Compress highlights (V>230 → 200-230)
        2. Enhance saturation (+20%) to recover color
        3. Increase contrast (CLAHE) for better separation

        Args:
            frame: BGR image from ESP32-CAM

        Returns:
            Enhanced BGR frame
        """
        try:
            # Convert to HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)

            # === Fix 1: Compress highlights (reduce overexposure) ===
            # Pixels with V>230 are likely overexposed
            # Compress 230-255 → 200-230 (preserve some brightness)
            overexposed_mask = v > 230
            if np.any(overexposed_mask):
                v_float = v.copy().astype(np.float32)
                # Formula: new_v = 200 + (old_v - 230) * 0.5
                v_float[overexposed_mask] = 200 + (v[overexposed_mask] - 230) * 0.5
                v = v_float.astype(np.uint8)

                logger.debug(
                    f"[FireDetector] Compressed {np.sum(overexposed_mask)} overexposed pixels"
                )

            # === Fix 2: Enhance saturation ===
            # Increase saturation by 20% to recover lost color
            # Yellow flame (S=100) → Enhanced (S=120) → Pass saturation filter
            s_float = s.copy().astype(np.float32)
            s_float = np.clip(s_float * 1.2, 0, 255)  # +20%
            s = s_float.astype(np.uint8)

            # === Fix 3: Enhance contrast (CLAHE) ===
            # Apply only to V channel (avoid color shift)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            v = clahe.apply(v)

            # Merge back and convert to BGR
            hsv_enhanced = cv2.merge([h, s, v])
            frame_enhanced = cv2.cvtColor(hsv_enhanced, cv2.COLOR_HSV2BGR)

            return frame_enhanced

        except Exception as e:
            logger.error(f"[FireDetector] Preprocessing error: {e}")
            return frame  # Return original on error

    def _detect_color_based(self, frame):
        """
        Color-based fire detection with motion analysis

        Steps:
        1. Detect fire colors (red/orange/yellow in HSV)
        2. Store current frame for motion comparison
        3. Analyze motion in detected fire regions
        4. Classify as DYNAMIC (real fire) or STATIC (image)
        """
        try:
            # === STEP 0: Preprocess frame ===
            # Fix ESP32-CAM overexposure issues
            frame_processed = self._preprocess_frame(frame)

            # Store current frame (grayscale) for motion analysis
            gray = cv2.cvtColor(frame_processed, cv2.COLOR_BGR2GRAY)
            self.prev_frames.append(gray)

            # === STEP 1: Color Detection ===
            hsv = cv2.cvtColor(frame_processed, cv2.COLOR_BGR2HSV)

            # Convert to LAB for better brightness analysis
            lab = cv2.cvtColor(frame_processed, cv2.COLOR_BGR2LAB)

            # === Fire color ranges (HSV) ===
            # Reference: https://en.wikipedia.org/wiki/Flame#Colour
            # Temperature → Color:
            # 1000-1500K: Red/Orange (wood fire, low temp)
            # 1500-2000K: Orange/Yellow (candle, wood fire)
            # 2000-3000K: Yellow/White (gas flame, high temp)
            # Note: Blue flames (3000K+, lighters) NOT detected - focus on typical fires

            masks = []

            # 1. RED flames (low temperature fire: wood, paper)
            # Hue: 0-10 (red lower bound)
            lower_red1 = np.array([0, 100, 100])  # [H, S, V]
            upper_red1 = np.array([10, 255, 255])
            masks.append(cv2.inRange(hsv, lower_red1, upper_red1))

            # Hue: 160-180 (red upper bound, wraps around)
            lower_red2 = np.array([160, 100, 100])
            upper_red2 = np.array([180, 255, 255])
            masks.append(cv2.inRange(hsv, lower_red2, upper_red2))

            # 2. ORANGE/YELLOW flames (medium temperature: candle, wood)
            # Hue: 10-30 (orange to yellow)
            lower_orange = np.array([10, 100, 100])
            upper_orange = np.array([30, 255, 255])
            masks.append(cv2.inRange(hsv, lower_orange, upper_orange))

            # 3. YELLOW/WHITE flames (high temperature: gas stove)
            # Hue: 30-40 (yellow), but LOWER saturation (more white)
            # Saturation: 50-150 (not fully saturated = white-ish)
            # Value: HIGH (bright)
            lower_yellow_white = np.array([25, 50, 180])
            upper_yellow_white = np.array([40, 150, 255])
            masks.append(cv2.inRange(hsv, lower_yellow_white, upper_yellow_white))
            # 4. WHITE/BRIGHT flames (intense heat core)
            # Low saturation (almost no color), very high brightness
            # This catches the BRIGHT CENTER of flames (any color)
            h_channel, s_channel, v_channel = cv2.split(hsv)

            # White = Low Saturation + High Value
            low_sat_mask = (s_channel < 100).astype(np.uint8) * 255
            high_val_mask = (v_channel > 200).astype(np.uint8) * 255
            white_mask = cv2.bitwise_and(low_sat_mask, high_val_mask)
            masks.append(white_mask)

            # 5. BRIGHT spots (using LAB color space)
            # L channel = Lightness (0=black, 255=white)
            # Flames are ALWAYS bright, regardless of color
            l_channel, _, _ = cv2.split(lab)
            # bright_mask = (l_channel > 180).astype(np.uint8) * 255
            # masks.append(bright_mask)
            # ❌ BỎ hoặc giảm ảnh hưởng ánh sáng mạnh
            bright_mask = (l_channel > 200).astype(np.uint8) * 255

            # chỉ giữ nếu có màu lửa đi kèm
            bright_mask = cv2.bitwise_and(bright_mask, high_sat_mask)

            masks.append(bright_mask)

            # === Combine all masks ===
            fire_mask = masks[0]
            for mask in masks[1:]:
                fire_mask = cv2.bitwise_or(fire_mask, mask)

            # === PHƯƠNG ÁN 2: Global Saturation Filter ===
            # Lửa (bất kể màu gì) LUÔN có saturation cao
            # Ánh sáng môi trường (trời, đèn, cửa sổ) có saturation thấp
            #
            # Ngưỡng saturation:
            # - Lửa đỏ/vàng: S > 100
            # - Lửa xanh: S > 120
            # - Ánh sáng trời/cửa sổ: S < 80 → BỊ LOẠI BỎ
            # - Đèn trắng: S < 50 → BỊ LOẠI BỎ
            min_saturation = 100  # Ngưỡng tối thiểu
            high_sat_mask = (s_channel > min_saturation).astype(np.uint8) * 255

            # AND với fire_mask (chỉ giữ vùng có màu đậm)
            fire_mask_before_sat_filter = fire_mask.copy()  # Backup for debugging
            fire_mask = cv2.bitwise_and(fire_mask, high_sat_mask)

            # Log saturation filtering effect
            pixels_before = np.sum(fire_mask_before_sat_filter > 0)
            pixels_after = np.sum(fire_mask > 0)
            if pixels_before > 0 and pixels_after < pixels_before:
                filtered_ratio = (pixels_before - pixels_after) / pixels_before * 100
                logger.debug(
                    f"[FireDetector] Saturation filter removed {filtered_ratio:.1f}% "
                    f"({pixels_before - pixels_after} pixels) - likely ambient light"
                )

            # Morphological operations to reduce noise
            kernel = np.ones((5, 5), np.uint8)
            fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_CLOSE, kernel)
            fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_OPEN, kernel)

            # === STEP 2: Motion Analysis ===
            motion_info = {"status": "motion_check_disabled"}
            if self.enable_motion_check:
                motion_info = self._analyze_motion_in_region(fire_mask)

            # === STEP 3: Find Contours ===
            contours, _ = cv2.findContours(
                fire_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            processed_frame = frame.copy()
            detections = []
            dynamic_fire_detected = False
            static_fire_detected = False

            # Filter contours by area
            min_area = 1200
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area:
                    continue

                # Get bounding box
                x, y, w, h = cv2.boundingRect(contour)

                # === STEP 4: Classify Detection ===
                if self.enable_motion_check:
                    is_dynamic = motion_info.get(
                        "has_motion", False
                    ) or motion_info.get("has_turbulence", False)
                else:
                    is_dynamic = (
                        True  # If motion check disabled, assume all fire is dynamic
                    )

                if is_dynamic:
                    # === DYNAMIC FIRE (Real Fire) ===
                    dynamic_fire_detected = True

                    # Draw RED bounding box
                    cv2.rectangle(
                        processed_frame, (x, y), (x + w, y + h), (0, 0, 255), 3
                    )

                    # Draw detailed label
                    label_lines = [
                        f"FIRE {area:.0f}px",
                        f"Motion: {motion_info.get('motion_ratio', 0) * 100:.1f}%",
                        f"Score: {motion_info.get('avg_motion_score', 0):.2f}",
                    ]

                    for i, line in enumerate(label_lines):
                        y_offset = y - 10 - (len(label_lines) - 1 - i) * 20
                        cv2.putText(
                            processed_frame,
                            line,
                            (x, y_offset),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (0, 0, 255),
                            2,
                        )

                    detections.append(
                        {
                            "class": "fire",
                            "type": "dynamic",
                            "confidence": min(area / 5000, 1.0),
                            "bbox": [x, y, x + w, y + h],
                            "area": int(area),
                            "motion_info": motion_info,
                        }
                    )

                else:
                    # === STATIC FIRE (Image/Poster) ===
                    static_fire_detected = True

                    # Draw GRAY bounding box
                    cv2.rectangle(
                        processed_frame, (x, y), (x + w, y + h), (128, 128, 128), 2
                    )

                    # Draw label
                    label_lines = [
                        f"STATIC {area:.0f}px",
                        f"Motion: {motion_info.get('motion_ratio', 0) * 100:.1f}%",
                        f"Score: {motion_info.get('avg_motion_score', 0):.2f}",
                    ]

                    for i, line in enumerate(label_lines):
                        y_offset = y - 10 - (len(label_lines) - 1 - i) * 20
                        cv2.putText(
                            processed_frame,
                            line,
                            (x, y_offset),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (128, 128, 128),
                            1,
                        )

                    detections.append(
                        {
                            "class": "fire",
                            "type": "static",
                            "confidence": 0.0,  # Static fire = not real
                            "bbox": [x, y, x + w, y + h],
                            "area": int(area),
                            "motion_info": motion_info,
                        }
                    )

            # === STEP 5: Update Detection State ===
            with self.detection_lock:
                self.dynamic_fire_detected = dynamic_fire_detected
                self.static_fire_detected = static_fire_detected
                self.fire_detected = (
                    dynamic_fire_detected  # Only DYNAMIC fire counts as real
                )

                if dynamic_fire_detected:
                    self.last_detection_time = time.time()
                    self.detection_count += 1

            # === STEP 6: Logging ===
            if dynamic_fire_detected:
                logger.warning(
                    f"[FireDetector] DYNAMIC FIRE DETECTED! "
                    f"Motion: {motion_info.get('motion_ratio', 0) * 100:.1f}%, "
                    f"Score: {motion_info.get('avg_motion_score', 0):.2f}, "
                    f"Turbulence: {motion_info.get('turbulence_variance', 0):.1f}"
                )
            elif static_fire_detected:
                logger.info(
                    f"[FireDetector] Static fire color detected (no motion) - "
                    f"Motion: {motion_info.get('motion_ratio', 0) * 100:.1f}%, "
                    f"Score: {motion_info.get('avg_motion_score', 0):.2f}"
                )

            return processed_frame, dynamic_fire_detected, detections

        except Exception as e:
            logger.error(f"[FireDetector] Color-based detection error: {e}")
            return frame, False, []

    def get_detection_status(self):
        """
        Get current detection status

        Returns:
            dict: Detection status with dynamic/static info
        """
        with self.detection_lock:
            return {
                "fire_detected": self.fire_detected,
                "dynamic_fire_detected": self.dynamic_fire_detected,
                "static_fire_detected": self.static_fire_detected,
                "last_detection_time": self.last_detection_time,
                "detection_count": self.detection_count,
                "time_since_detection": (
                    time.time() - self.last_detection_time
                    if self.last_detection_time > 0
                    else None
                ),
                "motion_history_size": len(self.motion_history),
                "avg_motion_score": (
                    float(np.mean(self.motion_history))
                    if len(self.motion_history) > 0
                    else 0.0
                ),
            }

    def reset_detection_count(self):
        """Reset detection counter and motion history"""
        with self.detection_lock:
            self.detection_count = 0
            self.motion_history.clear()
            self.prev_frames.clear()
            logger.info("[FireDetector] Detection count and motion history reset")
