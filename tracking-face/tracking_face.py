#!/usr/bin/env python3
import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2


def add_rdk_system_python_paths() -> None:
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    for path in [
        f"/usr/local/lib/python{py_ver}/dist-packages",
        f"/usr/lib/python{py_ver}/dist-packages",
        "/usr/lib/python3/dist-packages",
    ]:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


add_rdk_system_python_paths()

try:
    import Hobot.GPIO as GPIO
    from Hobot.GPIO import gpio as HOBOT_GPIO_INTERNALS
except ImportError:
    GPIO = None
    HOBOT_GPIO_INTERNALS = None


@dataclass
class ServoConfig:
    board_pin: int
    min_angle: float
    max_angle: float
    center_angle: float
    min_pulse_us: float
    max_pulse_us: float
    inverted: bool = False


class Servo:
    def __init__(self, config: ServoConfig, frequency_hz: int = 50, dry_run: bool = False):
        self.config = config
        self.frequency_hz = frequency_hz
        self.dry_run = dry_run
        self.angle = config.center_angle
        self.pwm = None

        if not dry_run:
            if GPIO is None:
                raise RuntimeError("Hobot.GPIO is not available. Use --dry-run to test without servos.")
            self.pwm = GPIO.PWM(config.board_pin, frequency_hz)
            self.pwm.start(self._angle_to_duty(config.center_angle))
            self._ensure_enabled()

    def _angle_to_duty(self, angle: float) -> float:
        angle = clamp(angle, self.config.min_angle, self.config.max_angle)
        if self.config.inverted:
            angle = self.config.max_angle - (angle - self.config.min_angle)

        span_angle = self.config.max_angle - self.config.min_angle
        ratio = (angle - self.config.min_angle) / span_angle if span_angle else 0.5
        pulse_us = self.config.min_pulse_us + ratio * (self.config.max_pulse_us - self.config.min_pulse_us)
        period_us = 1_000_000.0 / self.frequency_hz
        return 100.0 * pulse_us / period_us

    def move_to(self, angle: float) -> None:
        self.angle = clamp(angle, self.config.min_angle, self.config.max_angle)
        duty = self._angle_to_duty(self.angle)
        if self.dry_run:
            return
        self.pwm.ChangeDutyCycle(duty)
        self._ensure_enabled()

    def move_by(self, delta: float) -> None:
        self.move_to(self.angle + delta)

    def center(self) -> None:
        self.move_to(self.config.center_angle)

    def stop(self) -> None:
        if self.pwm is not None:
            self.pwm.stop()

    def _ensure_enabled(self) -> None:
        if HOBOT_GPIO_INTERNALS is not None:
            HOBOT_GPIO_INTERNALS._enable_pwm(self.config.board_pin)


class MotionAxis:
    def __init__(self, servo: Servo, max_speed_dps: float, max_accel_dps2: float, target_smoothing: float):
        self.servo = servo
        self.target_angle = servo.angle
        self.velocity = 0.0
        self.max_speed = max(1.0, max_speed_dps)
        self.max_accel = max(1.0, max_accel_dps2)
        self.target_smoothing = clamp(target_smoothing, 0.0, 1.0)

    def set_target(self, angle: float) -> None:
        angle = clamp(angle, self.servo.config.min_angle, self.servo.config.max_angle)
        alpha = self.target_smoothing
        self.target_angle = alpha * angle + (1.0 - alpha) * self.target_angle

    def reset(self) -> None:
        self.target_angle = self.servo.angle
        self.velocity = 0.0

    def update(self, dt: float) -> None:
        dt = clamp(dt, 0.001, 0.1)
        error = self.target_angle - self.servo.angle
        if abs(error) < 0.03 and abs(self.velocity) < 0.2:
            self.velocity = 0.0
            self.servo.move_to(self.target_angle)
            return

        direction = 1.0 if error > 0 else -1.0
        stopping_distance = (self.velocity * self.velocity) / (2.0 * self.max_accel)
        if abs(error) <= stopping_distance:
            desired_velocity = 0.0
        else:
            desired_velocity = direction * self.max_speed

        max_velocity_change = self.max_accel * dt
        self.velocity = approach(self.velocity, desired_velocity, max_velocity_change)
        step = self.velocity * dt
        if abs(step) > abs(error):
            step = error
            self.velocity = 0.0

        self.servo.move_by(step)


class FaceTracker:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.running = True
        self.detector_name = "haar"

        if args.show and not has_graphical_display():
            print("No graphical display found, disabling --show preview window.")
            self.args.show = False

        if GPIO is not None and not args.dry_run:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)

        self.face_detector = self._build_face_detector()

        self.pan_servo = Servo(
            ServoConfig(
                board_pin=args.pan_pin,
                min_angle=args.pan_min,
                max_angle=args.pan_max,
                center_angle=args.pan_center,
                min_pulse_us=args.servo_min_us,
                max_pulse_us=args.servo_max_us,
                inverted=not args.normal_pan,
            ),
            frequency_hz=args.servo_frequency,
            dry_run=args.dry_run,
        )
        self.tilt_servo = Servo(
            ServoConfig(
                board_pin=args.tilt_pin,
                min_angle=args.tilt_min,
                max_angle=args.tilt_max,
                center_angle=args.tilt_center,
                min_pulse_us=args.servo_min_us,
                max_pulse_us=args.servo_max_us,
                inverted=args.invert_tilt,
            ),
            frequency_hz=args.servo_frequency,
            dry_run=args.dry_run,
        )
        self.pan_axis = MotionAxis(
            self.pan_servo,
            args.pan_max_speed,
            args.pan_max_accel,
            args.target_filter_alpha,
        )
        self.tilt_axis = MotionAxis(
            self.tilt_servo,
            args.tilt_max_speed,
            args.tilt_max_accel,
            args.target_filter_alpha,
        )

        self.filtered_error_x = 0.0
        self.filtered_error_y = 0.0
        self.has_filtered_error = False

    def _build_face_detector(self):
        if self.args.detector in {"yunet", "auto"}:
            model_path = Path(self.args.yunet_model)
            if model_path.exists() and hasattr(cv2, "FaceDetectorYN"):
                try:
                    detector = cv2.FaceDetectorYN.create(
                        str(model_path),
                        "",
                        (self.args.camera_width, self.args.camera_height),
                        self.args.yunet_score_threshold,
                        self.args.yunet_nms_threshold,
                        self.args.yunet_top_k,
                    )
                    self.detector_name = "yunet"
                    print(f"Using YuNet face detector: {model_path}")
                    return detector
                except Exception as exc:
                    if self.args.detector == "yunet":
                        raise RuntimeError(f"Failed to initialize YuNet detector: {exc}")
                    print(f"YuNet unavailable, fallback to Haar: {exc}")
            elif self.args.detector == "yunet":
                raise RuntimeError(f"YuNet model not found: {model_path}")

        cascade_path = self.args.cascade or str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        detector = cv2.CascadeClassifier(cascade_path)
        if detector.empty():
            raise RuntimeError(f"Failed to load Haar cascade: {cascade_path}")
        self.detector_name = "haar"
        print(f"Using Haar face detector: {cascade_path}")
        return detector

    def open_camera(self):
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.args.camera_index, backend)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open camera /dev/video{self.args.camera_index}")

        if self.args.camera_fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.args.camera_fourcc[:4].ljust(4)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.camera_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.camera_height)
        cap.set(cv2.CAP_PROP_FPS, self.args.camera_fps)

        return cap

    def select_face(self, faces) -> Optional[Tuple[int, int, int, int]]:
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
        return int(x), int(y), int(w), int(h)

    def detect_faces(self, frame):
        if self.detector_name == "yunet":
            h, w = frame.shape[:2]
            self.face_detector.setInputSize((w, h))
            _, detections = self.face_detector.detect(frame)
            if detections is None:
                return []
            return detections[:, :4]

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        return self.face_detector.detectMultiScale(
            gray,
            scaleFactor=self.args.scale_factor,
            minNeighbors=self.args.min_neighbors,
            minSize=(self.args.min_face, self.args.min_face),
        )

    def track_face(self, frame, face: Tuple[int, int, int, int]) -> None:
        h, w = frame.shape[:2]
        x, y, fw, fh = face
        face_cx = x + fw / 2.0
        face_cy = y + fh / 2.0

        error_x = (face_cx - w / 2.0) / (w / 2.0)
        error_y = (face_cy - h / 2.0) / (h / 2.0)
        error_x, error_y = self.filter_error(error_x, error_y)

        if abs(error_x) > self.args.dead_zone:
            self.pan_axis.set_target(self.pan_servo.angle + error_x * self.args.pan_gain)
        if abs(error_y) > self.args.dead_zone:
            self.tilt_axis.set_target(self.tilt_servo.angle + error_y * self.args.tilt_gain)
    def filter_error(self, error_x: float, error_y: float) -> Tuple[float, float]:
        alpha = clamp(self.args.error_filter_alpha, 0.0, 1.0)
        if not self.has_filtered_error:
            self.filtered_error_x = error_x
            self.filtered_error_y = error_y
            self.has_filtered_error = True
        else:
            self.filtered_error_x = alpha * error_x + (1.0 - alpha) * self.filtered_error_x
            self.filtered_error_y = alpha * error_y + (1.0 - alpha) * self.filtered_error_y
        return self.filtered_error_x, self.filtered_error_y

    def draw_overlay(self, frame, face: Optional[Tuple[int, int, int, int]], fps: float) -> None:
        h, w = frame.shape[:2]
        cv2.line(frame, (w // 2 - 16, h // 2), (w // 2 + 16, h // 2), (255, 255, 255), 1)
        cv2.line(frame, (w // 2, h // 2 - 16), (w // 2, h // 2 + 16), (255, 255, 255), 1)

        if face is not None:
            x, y, fw, fh = face
            cv2.rectangle(frame, (x, y), (x + fw, y + fh), (0, 220, 0), 2)
            cv2.circle(frame, (x + fw // 2, y + fh // 2), 4, (0, 220, 0), -1)

        info = (
            f"{self.detector_name} pan={self.pan_servo.angle:.1f}->{self.pan_axis.target_angle:.1f} "
            f"tilt={self.tilt_servo.angle:.1f}->{self.tilt_axis.target_angle:.1f} fps={fps:.1f}"
        )
        cv2.putText(frame, info, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)

    def maybe_publish_frame(self, frame, now: float) -> None:
        if not self.args.publish_frame:
            return
        if now - self.last_publish_at < self.args.publish_interval:
            return

        publish_path = Path(self.args.publish_frame)
        publish_path.parent.mkdir(parents=True, exist_ok=True)

        quality = clamp(self.args.publish_jpeg_quality, 30, 95)
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return

        tmp_path = publish_path.with_suffix(publish_path.suffix + ".tmp")
        tmp_path.write_bytes(encoded.tobytes())
        os.replace(tmp_path, publish_path)
        self.last_publish_at = now

    def run(self) -> None:
        self.pan_servo.center()
        self.tilt_servo.center()
        time.sleep(self.args.center_delay)

        cap = self.open_camera()
        print(
            f"Camera /dev/video{self.args.camera_index} opened at "
            f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
        )
        print(f"PWM6 tilt: BOARD {self.args.tilt_pin}; PWM7 pan: BOARD {self.args.pan_pin}")
        print(f"Face detector: {self.detector_name}")

        last_loop_at = time.time()
        self.last_publish_at = 0.0
        fps = 0.0
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    print("Camera frame read failed, retrying...")
                    time.sleep(0.05)
                    continue

                now = time.time()
                dt = max(1e-6, now - last_loop_at)
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
                last_loop_at = now

                faces = self.detect_faces(frame)
                face = self.select_face(faces)
                if face is not None:
                    self.track_face(frame, face)

                self.pan_axis.update(dt)
                self.tilt_axis.update(dt)
                self.maybe_publish_frame(frame, now)

                if self.args.show:
                    self.draw_overlay(frame, face, fps)
                    cv2.imshow("RDK X5 face tracking", frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break

                if self.args.loop_sleep > 0:
                    time.sleep(self.args.loop_sleep)
        finally:
            cap.release()
            cv2.destroyAllWindows()
            self.pan_servo.center()
            self.tilt_servo.center()
            time.sleep(self.args.center_delay)
            self.pan_servo.stop()
            self.tilt_servo.stop()
            if GPIO is not None and not self.args.dry_run:
                GPIO.cleanup()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def approach(current: float, target: float, step: float) -> float:
    if abs(target - current) <= step:
        return target
    return current + step if target > current else current - step


def has_graphical_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RDK X5 USB camera face tracker with PWM6/PWM7 servos.")

    parser.add_argument("--camera-index", type=int, default=0, help="USB camera index, usually /dev/video0.")
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=360)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--camera-fourcc", default="MJPG")
    parser.add_argument("--cascade", default="", help="Optional OpenCV Haar cascade XML path.")
    parser.add_argument("--detector", choices=("auto", "yunet", "haar"), default="auto")
    parser.add_argument("--yunet-model", default=str(Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"))
    parser.add_argument("--yunet-score-threshold", type=float, default=0.85)
    parser.add_argument("--yunet-nms-threshold", type=float, default=0.3)
    parser.add_argument("--yunet-top-k", type=int, default=5000)
    parser.add_argument("--show", action="store_true", help="Show preview window. Press q or Esc to quit.")
    parser.add_argument("--dry-run", action="store_true", help="Run camera and face detection without servo PWM output.")
    parser.add_argument("--publish-frame", default="/tmp/rdk_shared_camera/latest.jpg", help="Write latest camera frame for other programs.")
    parser.add_argument("--publish-interval", type=float, default=0.2, help="Minimum seconds between published frames.")
    parser.add_argument("--publish-jpeg-quality", type=int, default=80)

    parser.add_argument("--tilt-pin", type=int, default=32, help="PWM6 physical BOARD pin for up/down tilt servo.")
    parser.add_argument("--pan-pin", type=int, default=33, help="PWM7 physical BOARD pin for left/right pan servo.")
    parser.add_argument("--servo-frequency", type=int, default=50)
    parser.add_argument("--servo-min-us", type=float, default=500.0)
    parser.add_argument("--servo-max-us", type=float, default=2500.0)

    parser.add_argument("--pan-min", type=float, default=20.0)
    parser.add_argument("--pan-max", type=float, default=160.0)
    parser.add_argument("--pan-center", type=float, default=90.0)
    parser.add_argument("--tilt-min", type=float, default=35.0)
    parser.add_argument("--tilt-max", type=float, default=145.0)
    parser.add_argument("--tilt-center", type=float, default=90.0)
    parser.add_argument("--normal-pan", action="store_true", help="Use non-inverted pan servo direction.")
    parser.add_argument("--invert-tilt", action="store_true", help="Reverse tilt servo direction.")

    parser.add_argument("--pan-gain", type=float, default=2.2, help="Degrees per normalized horizontal error.")
    parser.add_argument("--tilt-gain", type=float, default=1.8, help="Degrees per normalized vertical error.")
    parser.add_argument("--dead-zone", type=float, default=0.08, help="Ignore small normalized face-center errors.")
    parser.add_argument("--error-filter-alpha", type=float, default=0.38, help="EMA factor for face-center error. Lower is smoother.")
    parser.add_argument("--target-filter-alpha", type=float, default=0.82, help="EMA factor for servo target angle. Lower is smoother.")
    parser.add_argument("--pan-max-speed", type=float, default=190.0, help="Maximum pan speed in degrees per second.")
    parser.add_argument("--tilt-max-speed", type=float, default=150.0, help="Maximum tilt speed in degrees per second.")
    parser.add_argument("--pan-max-accel", type=float, default=700.0, help="Maximum pan acceleration in degrees per second squared.")
    parser.add_argument("--tilt-max-accel", type=float, default=560.0, help="Maximum tilt acceleration in degrees per second squared.")
    parser.add_argument("--center-delay", type=float, default=0.3)
    parser.add_argument("--loop-sleep", type=float, default=0.005)

    parser.add_argument("--scale-factor", type=float, default=1.12)
    parser.add_argument("--min-neighbors", type=int, default=5)
    parser.add_argument("--min-face", type=int, default=60)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tracker = FaceTracker(args)

    def stop(_signum, _frame):
        tracker.running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        tracker.run()
    except Exception as exc:
        print(f"tracking_face error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
