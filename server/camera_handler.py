"""
Camera Handler Module
Manages camera stream proxy from ESP32-CAM to Frontend
Prepares for future AI processing integration
"""

from asyncio.windows_events import NULL
import logging
import time
from threading import Lock, Thread

import cv2
import numpy as np
import requests

from config import AI_CONFIDENCE_THRESHOLD, AI_PROCESS_EVERY_N_FRAMES
from fire_detector import FireDetector

logger = logging.getLogger(__name__)


class CameraHandler:
    """
    Camera Stream Handler
    - Fetches MJPEG stream from ESP32-CAM
    - Provides dual stream: original and processed (for future AI)
    - Thread-safe frame access
    """

    def __init__(self, esp32_cam_url):
        """
        Initialize Camera Handler

        Args:
            esp32_cam_url: Base URL of ESP32-CAM (e.g., "http://192.168.1.100")
        """
        self.esp32_cam_url = esp32_cam_url
        self.stream_url = f"{esp32_cam_url}/stream"
        self.status_url = f"{esp32_cam_url}/status"

        # Frame storage
        self.latest_frame = None
        self.processed_frame = None

        # Thread control
        self.is_running = False
        self.fetch_thread = None
        self.frame_lock = Lock()

        # Stats
        self.fps = 0
        self.frame_count = 0
        self.last_fps_time = time.time()

        # Frame skip optimization (for slow CPU)
        self.ai_frame_count = 0  # Counter for AI processing
        self.process_every_n_frames = AI_PROCESS_EVERY_N_FRAMES  # From config.py

        # Fire detection
        # Try to load YOLO model, fallback to color-based if not available
        import os

        # yolo_model_path = os.path.join(
        #     os.path.dirname(__file__), "models", "yolov5s_best.pt"
        # )
        yolo_model_path = ""

        if os.path.exists(yolo_model_path):
            logger.info(
                f"[CameraHandler] Loading YOLO fire detection model: {yolo_model_path}"
            )
            self.fire_detector = FireDetector(
                model_path=yolo_model_path,
                confidence_threshold=AI_CONFIDENCE_THRESHOLD,
                enable_motion_check=True,
            )
        else:
            logger.warning(f"[CameraHandler] YOLO model not found at {yolo_model_path}")
            logger.warning("[CameraHandler] Using color-based fire detection (HSV)")
            self.fire_detector = FireDetector(
                model_path=None,  # No model = color-based
                confidence_threshold=AI_CONFIDENCE_THRESHOLD,
                enable_motion_check=True,
            )

        # Detection results
        self.fire_detected = False
        self.detections = []

        logger.info(f"[Camera] Handler initialized for {esp32_cam_url}")
        logger.info(
            f"[Camera] AI frame skip: Process 1 out of every {self.process_every_n_frames} frames"
        )

    def start(self):
        """
        Start background thread to fetch frames from ESP32-CAM
        """
        if self.is_running:
            logger.warning("[Camera] Already running")
            return

        self.is_running = True
        self.fetch_thread = Thread(target=self._fetch_frames, daemon=True)
        self.fetch_thread.start()
        logger.info("[Camera] Frame fetching thread started")

    def stop(self):
        """
        Stop background thread
        """
        self.is_running = False
        if self.fetch_thread:
            self.fetch_thread.join(timeout=2)
        logger.info("[Camera] Frame fetching thread stopped")

    def _fetch_frames(self):
        """
        Background thread: continuously fetch frames from ESP32-CAM MJPEG stream
        """
        logger.info(f"[Camera] Connecting to {self.stream_url}")

        while self.is_running:
            try:
                # Connect to ESP32-CAM stream
                response = requests.get(self.stream_url, stream=True, timeout=5)

                if response.status_code != 200:
                    logger.error(f"[Camera] HTTP {response.status_code} from ESP32-CAM")
                    time.sleep(2)
                    continue

                logger.info("[Camera] Connected to ESP32-CAM stream")

                # Parse MJPEG stream
                bytes_data = b""
                for chunk in response.iter_content(chunk_size=1024):
                    if not self.is_running:
                        break

                    bytes_data += chunk

                    # Find JPEG boundaries
                    a = bytes_data.find(b"\xff\xd8")  # JPEG start marker
                    b = bytes_data.find(b"\xff\xd9")  # JPEG end marker

                    if a != -1 and b != -1:
                        # Extract JPEG image
                        jpg = bytes_data[a : b + 2]
                        bytes_data = bytes_data[b + 2 :]

                        # Validate JPEG: must have valid markers and min size
                        if len(jpg) < 100:  # Too small to be valid JPEG
                            continue

                        if jpg[0:2] != b"\xff\xd8" or jpg[-2:] != b"\xff\xd9":
                            logger.warning(
                                "[Camera] Invalid JPEG markers, skipping frame"
                            )
                            continue

                        # Decode JPEG to numpy array (OpenCV format)
                        try:
                            frame = cv2.imdecode(
                                np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR
                            )
                        except Exception as e:
                            logger.warning(f"[Camera] imdecode failed: {e}")
                            continue

                        if frame is not None:
                            self.ai_frame_count += 1

                            # Only process every N frames for AI (skip frames to reduce CPU load)
                            if self.ai_frame_count % self.process_every_n_frames == 0:
                                # Run fire detection
                                processed, fire_detected, detections = (
                                    self.fire_detector.detect_fire(frame)
                                )

                                # Update frames with thread safety
                                with self.frame_lock:
                                    self.latest_frame = frame
                                    self.processed_frame = processed
                                    self.fire_detected = fire_detected
                                    self.detections = detections

                                # Log fire detection
                                if fire_detected:
                                    logger.warning(
                                        f"[FireDetector] FIRE DETECTED! {len(detections)} detection(s)"
                                    )
                            else:
                                # Skip AI processing, just update raw frame
                                with self.frame_lock:
                                    self.latest_frame = frame
                                    # Keep previous processed_frame for display
                                    # (shows last AI result until next process)

                            # Update FPS stats
                            self._update_fps()

            except requests.exceptions.Timeout:
                logger.warning("[Camera] Connection timeout to ESP32-CAM")
                time.sleep(2)
            except requests.exceptions.ConnectionError:
                logger.error("[Camera] Cannot connect to ESP32-CAM")
                time.sleep(5)
            except Exception as e:
                logger.error(f"[Camera] Error fetching frames: {e}")
                time.sleep(2)

        logger.info("[Camera] Frame fetching loop ended")

    def _update_fps(self):
        """
        Update FPS counter
        """
        self.frame_count += 1
        current_time = time.time()
        elapsed = current_time - self.last_fps_time

        if elapsed >= 1.0:  # Update FPS every second
            self.fps = self.frame_count / elapsed
            ai_fps = self.fps / self.process_every_n_frames  # Actual AI processing FPS
            self.frame_count = 0
            self.last_fps_time = current_time
            logger.info(f"[Camera] Total FPS: {self.fps:.1f} | AI FPS: {ai_fps:.1f}")

    def _enhance_image(self, frame):
        """
        Enhance dark images using gamma correction and CLAHE

        Args:
            frame: OpenCV BGR image

        Returns:
            Enhanced image
        """
        # Convert BGR to LAB color space
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)

        # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to L channel
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l)

        # Merge channels back
        enhanced_lab = cv2.merge([l_enhanced, a, b])

        # Convert back to BGR
        enhanced = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

        # Optional: Apply gamma correction for brightness boost
        gamma = 1.2  # >1 brightens, <1 darkens
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype(
            "uint8"
        )
        enhanced = cv2.LUT(enhanced, table)

        return enhanced

    def generate_original_stream(self):
        """
        Generator for original MJPEG stream
        Yields frames in MJPEG format for Flask Response

        Yields:
            bytes: MJPEG frame with multipart boundary
        """
        logger.info("[Camera] Original stream client connected")

        while True:
            with self.frame_lock:
                if self.latest_frame is not None:
                    # Encode frame to JPEG
                    ret, jpeg = cv2.imencode(
                        ".jpg", self.latest_frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
                    )

                    if ret:
                        # MJPEG multipart format
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n"
                            + jpeg.tobytes()
                            + b"\r\n"
                        )

            # Limit FPS to ~30 to reduce bandwidth
            time.sleep(0.033)

    def generate_processed_stream(self):
        """
        Generator for AI-processed MJPEG stream
        Future: will contain bounding boxes from YOLO

        Yields:
            bytes: MJPEG frame with multipart boundary
        """
        logger.info("[Camera] Processed stream client connected")

        while True:
            with self.frame_lock:
                if self.processed_frame is not None:
                    # Encode frame to JPEG
                    ret, jpeg = cv2.imencode(
                        ".jpg", self.processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
                    )

                    if ret:
                        # MJPEG multipart format
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n"
                            + jpeg.tobytes()
                            + b"\r\n"
                        )

            # Limit FPS to ~30
            time.sleep(0.033)

    def get_camera_status(self):
        """
        Get camera status from ESP32-CAM

        Returns:
            dict: Camera status or error
        """
        try:
            response = requests.get(self.status_url, timeout=2)
            if response.status_code == 200:
                status = response.json()
                status["fps"] = round(self.fps, 1)
                status["backend_running"] = self.is_running
                status["fire_detected"] = self.fire_detected
                status["detection_count"] = len(self.detections)
                return status
            else:
                return {"camera": "offline", "error": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"camera": "offline", "error": str(e)}

    def get_fire_status(self):
        """
        Get fire detection status

        Returns:
            dict: Fire detection status
        """
        detection_status = self.fire_detector.get_detection_status()
        detection_status["current_detections"] = self.detections
        return detection_status
