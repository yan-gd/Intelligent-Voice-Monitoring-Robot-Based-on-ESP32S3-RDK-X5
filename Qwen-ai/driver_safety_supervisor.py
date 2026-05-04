#!/usr/bin/env python3
import base64
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import requests
from dotenv import load_dotenv


@dataclass
class AppConfig:
    api_key: str
    vision_model: str
    camera_source: str
    camera_index: int
    camera_probe_max: int
    camera_width: int
    camera_height: int
    camera_fps: int
    camera_fourcc: str
    camera_drain_frames: int
    capture_thread_interval_sec: float
    interval_sec: float
    upload_width: int
    upload_height: int
    upload_jpeg_quality: int
    request_connect_timeout_sec: float
    request_timeout_sec: float
    infer_min_interval_sec: float
    thinking_enabled: bool
    api_base_url: str
    shared_frame_path: Path
    shared_frame_wait_sec: float
    runtime_dir: Path
    tts_dir: Path


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_config() -> AppConfig:
    load_dotenv()

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY in environment.")

    runtime_dir = Path(os.getenv("RUNTIME_DIR", "runtime")).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    tts_dir = Path(os.getenv("TTS_DIR", "tts")).resolve()

    return AppConfig(
        api_key=api_key,
        vision_model=os.getenv("VISION_MODEL", "qwen3.6-plus"),
        camera_source=os.getenv("CAMERA_SOURCE", "usb").strip().lower(),
        camera_index=int(os.getenv("CAMERA_INDEX", "0")),
        camera_probe_max=int(os.getenv("CAMERA_PROBE_MAX", "6")),
        camera_width=int(os.getenv("CAMERA_WIDTH", "640")),
        camera_height=int(os.getenv("CAMERA_HEIGHT", "360")),
        camera_fps=int(os.getenv("CAMERA_FPS", "30")),
        camera_fourcc=os.getenv("CAMERA_FOURCC", "MJPG").strip().upper(),
        camera_drain_frames=int(os.getenv("CAMERA_DRAIN_FRAMES", "8")),
        capture_thread_interval_sec=float(os.getenv("CAPTURE_THREAD_INTERVAL_SEC", "0.03")),
        interval_sec=float(os.getenv("CAPTURE_INTERVAL_SEC", "1")),
        upload_width=int(os.getenv("UPLOAD_WIDTH", "640")),
        upload_height=int(os.getenv("UPLOAD_HEIGHT", "360")),
        upload_jpeg_quality=int(os.getenv("UPLOAD_JPEG_QUALITY", "55")),
        request_connect_timeout_sec=float(os.getenv("REQUEST_CONNECT_TIMEOUT_SEC", "0.8")),
        request_timeout_sec=float(os.getenv("REQUEST_TIMEOUT_SEC", "2.5")),
        infer_min_interval_sec=float(os.getenv("INFER_MIN_INTERVAL_SEC", "3")),
        thinking_enabled=env_bool("THINKING_ENABLED", False),
        api_base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        shared_frame_path=Path(os.getenv("SHARED_FRAME_PATH", "/tmp/rdk_shared_camera/latest.jpg")).resolve(),
        shared_frame_wait_sec=float(os.getenv("SHARED_FRAME_WAIT_SEC", "5")),
        runtime_dir=runtime_dir,
        tts_dir=tts_dir,
    )


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def frame_to_data_url(config: AppConfig, frame) -> str:
    target_w = max(1, config.upload_width)
    target_h = max(1, config.upload_height)
    if frame.shape[1] != target_w or frame.shape[0] != target_h:
        frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

    quality = min(95, max(30, config.upload_jpeg_quality))
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("Failed to encode frame to JPEG.")
    b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def extract_json_text(raw_text: str) -> str:
    raw_text = raw_text.strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.S)
    if fenced:
        return fenced.group(1)

    direct = re.search(r"\{.*\}", raw_text, re.S)
    if direct:
        return direct.group(0)

    return raw_text


def normalize_category(raw_category: str) -> str:
    category = raw_category.strip().lower()
    mapping = {
        "normal": "normal",
        "angry": "angry",
        "eyes_closed": "eyes_closed",
        "phone_use": "phone_use",
        "drinking": "drinking",
        "愤怒": "angry",
        "生气": "angry",
        "闭眼": "eyes_closed",
        "疲劳": "eyes_closed",
        "玩手机": "phone_use",
        "看手机": "phone_use",
        "喝水": "drinking",
    }
    return mapping.get(category, "normal")


def analyze_driver_status(config: AppConfig, frame, session: requests.Session) -> Tuple[bool, str, str]:
    data_url = frame_to_data_url(config, frame)

    system_prompt = (
        "判断驾驶行为类别，只能是 normal、angry、eyes_closed、phone_use、drinking。"
        "仅输出 JSON。"
    )

    user_prompt = '{"category":"normal|angry|eyes_closed|phone_use|drinking"}'

    payload = {
        "model": config.vision_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    if not config.thinking_enabled:
        payload["enable_thinking"] = False

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    url = f"{config.api_base_url}/chat/completions"
    resp = session.post(
        url,
        headers=headers,
        json=payload,
        timeout=(config.request_connect_timeout_sec, config.request_timeout_sec),
    )
    resp.raise_for_status()

    body = resp.json()
    raw_content = body["choices"][0]["message"]["content"]

    if isinstance(raw_content, list):
        # Compatibility for models returning structured content blocks.
        raw_text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw_content
        )
    else:
        raw_text = str(raw_content)

    parsed_text = extract_json_text(raw_text)

    category = "normal"

    try:
        parsed = json.loads(parsed_text)
        category = normalize_category(str(parsed.get("category", "normal")))
    except json.JSONDecodeError:
        fallback_mapping = [
            ("angry", ["愤怒", "生气", "angry"]),
            ("eyes_closed", ["闭眼", "疲劳", "eyes_closed"]),
            ("phone_use", ["玩手机", "看手机", "phone_use"]),
            ("drinking", ["喝水", "drinking"]),
        ]
        for name, keys in fallback_mapping:
            if any(k in raw_text for k in keys):
                category = name
                break

    abnormal = category != "normal"
    return abnormal, category, raw_text


def choose_local_audio(config: AppConfig, category: str) -> Path:
    category_dir = config.tts_dir / category
    if not category_dir.exists() or not category_dir.is_dir():
        raise RuntimeError(f"Audio category directory not found: {category_dir}")

    audio_files: List[Path] = []
    for pattern in ["*.mp3", "*.wav", "*.m4a", "*.ogg"]:
        audio_files.extend(sorted(category_dir.glob(pattern)))

    if not audio_files:
        raise RuntimeError(f"No audio files in category directory: {category_dir}")

    return random.choice(audio_files)


def play_audio(audio_path: Path) -> None:
    player_cmds = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(audio_path)],
        ["mpg123", "-q", str(audio_path)],
        ["mpv", "--no-video", "--really-quiet", str(audio_path)],
        ["cvlc", "--play-and-exit", str(audio_path)],
        ["aplay", str(audio_path)],
        ["paplay", str(audio_path)],
    ]

    for cmd in player_cmds:
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False)
            return

    log("No audio player found (ffplay/aplay/paplay). Alert audio saved but not played.")


def save_snapshot(config: AppConfig, frame) -> Path:
    snap_path = config.runtime_dir / "latest.jpg"
    cv2.imwrite(str(snap_path), frame)
    return snap_path


def get_camera_candidates(config: AppConfig) -> List[int]:
    candidates: List[int] = []
    available_video_nodes = {
        int(m.group(1))
        for p in glob.glob("/dev/video*")
        for m in [re.search(r"/dev/video(\d+)$", p)]
        if m
    }

    if config.camera_index >= 0 and config.camera_index in available_video_nodes:
        candidates.append(config.camera_index)

    for dev_path in sorted(glob.glob("/dev/video*")):
        matched = re.search(r"/dev/video(\d+)$", dev_path)
        if not matched:
            continue
        idx = int(matched.group(1))
        if idx not in candidates:
            candidates.append(idx)

    if available_video_nodes:
        for idx in range(max(0, config.camera_probe_max)):
            if idx not in candidates and idx in available_video_nodes:
                candidates.append(idx)

    return candidates


def describe_video_nodes() -> str:
    nodes = sorted(glob.glob("/dev/video*"))
    if not nodes:
        return "none"
    return ", ".join(nodes)


class UsbCameraSource:
    def __init__(self, cap):
        self.cap = cap

    def read(self, drain_frames: int = 1):
        # Drain buffered frames first, so retrieve() returns the most recent image.
        for _ in range(max(0, drain_frames - 1)):
            if not self.cap.grab():
                break
        return self.cap.read()

    def release(self) -> None:
        self.cap.release()


class SharedJpegFrameSource:
    def __init__(self, frame_path: Path, wait_sec: float):
        self.frame_path = frame_path
        self.wait_sec = max(0.0, wait_sec)
        self.last_mtime_ns = -1

    def read(self, drain_frames: int = 1):
        deadline = time.time() + self.wait_sec
        while True:
            try:
                stat = self.frame_path.stat()
                if stat.st_size > 0 and stat.st_mtime_ns != self.last_mtime_ns:
                    frame = cv2.imread(str(self.frame_path))
                    if frame is not None:
                        self.last_mtime_ns = stat.st_mtime_ns
                        return True, frame
            except FileNotFoundError:
                pass

            if time.time() >= deadline:
                return False, None
            time.sleep(0.03)

    def release(self) -> None:
        return


def open_usb_camera(config: AppConfig):
    errors: List[str] = []
    candidates = get_camera_candidates(config)
    if not candidates:
        raise RuntimeError(
            "No /dev/video* candidates available for USB camera. "
            "The camera is visible on USB only after the kernel creates a video device. "
            "Check: ls -l /dev/video*; v4l2-ctl --list-devices; dmesg | grep -iE 'uvc|video|1b3f|2002'."
        )

    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    for idx in candidates:
        log(f"Trying USB camera /dev/video{idx}...")
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            errors.append(f"/dev/video{idx}: open failed")
            cap.release()
            continue

        if config.camera_fourcc:
            fourcc = cv2.VideoWriter_fourcc(*config.camera_fourcc[:4].ljust(4))
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        if config.camera_width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.camera_width)
        if config.camera_height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.camera_height)
        if config.camera_fps > 0:
            cap.set(cv2.CAP_PROP_FPS, config.camera_fps)

        ok = False
        for _ in range(5):
            ok, _ = UsbCameraSource(cap).read(drain_frames=1)
            if ok:
                break
            time.sleep(0.1)

        if ok:
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            log(f"USB camera /dev/video{idx} opened at {actual_w}x{actual_h}@{actual_fps:.1f}.")
            return UsbCameraSource(cap), f"usb:{idx}"

        errors.append(f"/dev/video{idx}: opened but read failed")
        cap.release()

    raise RuntimeError(
        f"No readable USB camera found. visible_nodes={describe_video_nodes()}; "
        f"candidates={candidates}; errors={'; '.join(errors)}"
    )


def open_working_camera(config: AppConfig):
    if config.camera_source in {"shared", "shared_jpeg", "file"}:
        return SharedJpegFrameSource(config.shared_frame_path, config.shared_frame_wait_sec), f"shared_jpeg:{config.shared_frame_path}"
    return open_usb_camera(config)


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    log("Starting driver safety supervisor...")
    log(
        f"Vision model: {config.vision_model} | Camera: USB | "
        f"Camera source: {config.camera_source} | "
        f"Camera target: /dev/video{config.camera_index}, "
        f"Shared frame: {config.shared_frame_path} | "
        f"{config.camera_width}x{config.camera_height}@{config.camera_fps}, fourcc={config.camera_fourcc or 'default'} | "
        f"Drain frames: {config.camera_drain_frames} | "
        f"Capture thread interval: {config.capture_thread_interval_sec}s | "
        f"Upload: {config.upload_width}x{config.upload_height}@q{config.upload_jpeg_quality} | "
        f"Req timeout: {config.request_timeout_sec}s | Infer interval: {config.infer_min_interval_sec}s | "
        f"Thinking enabled: {config.thinking_enabled} | Local audio dir: {config.tts_dir}"
    )

    try:
        camera, camera_desc = open_working_camera(config)
        log(f"Using camera: {camera_desc}")
    except Exception as exc:
        print(f"Failed to open camera: {exc}", file=sys.stderr)
        return 1

    state_lock = threading.Lock()
    latest_capture_frame: Optional[object] = None
    latest_capture_ts: float = 0.0
    latest_frame: Optional[object] = None
    latest_frame_ts: float = 0.0
    stop_event = threading.Event()

    def capture_worker() -> None:
        nonlocal camera, camera_desc, latest_capture_frame, latest_capture_ts
        while not stop_event.is_set():
            ok, frame = camera.read(drain_frames=max(1, config.camera_drain_frames))
            if not ok:
                if config.camera_source in {"shared", "shared_jpeg", "file"}:
                    log("No fresh shared frame available yet.")
                    stop_event.wait(0.3)
                    continue
                log("Camera frame capture failed in capture thread, trying to reopen camera.")
                camera.release()
                try:
                    camera, camera_desc = open_working_camera(config)
                    log(f"Camera reopened successfully: {camera_desc}")
                except Exception as exc:
                    log(f"Camera reopen failed in capture thread: {exc}")
                    stop_event.wait(0.3)
                continue

            with state_lock:
                latest_capture_frame = frame
                latest_capture_ts = time.time()

            pause = max(0.0, config.capture_thread_interval_sec)
            if pause > 0:
                stop_event.wait(pause)

    def inference_worker() -> None:
        nonlocal latest_frame, latest_frame_ts
        session = requests.Session()
        last_infer_at = 0.0
        while not stop_event.is_set():
            frame = None
            frame_ts = 0.0

            now = time.time()
            min_gap = max(0.0, config.infer_min_interval_sec)
            if now - last_infer_at < min_gap:
                stop_event.wait(min(0.05, min_gap - (now - last_infer_at)))
                continue

            with state_lock:
                if latest_frame is not None:
                    frame = latest_frame
                    frame_ts = latest_frame_ts
                    latest_frame = None

            if frame is None:
                stop_event.wait(0.02)
                continue

            try:
                last_infer_at = time.time()
                abnormal, category, reason_text = analyze_driver_status(config, frame, session)
                latency = time.time() - frame_ts
                log(f"Vision result abnormal={abnormal}; category={category}; latency={latency:.2f}s; raw={reason_text}")
                if abnormal:
                    audio = choose_local_audio(config, category)
                    log(f"Abnormal detected: {category}, playing: {audio.name}")
                    play_audio(audio)
                else:
                    log("No abnormal state, no speech generated.")
            except requests.Timeout:
                log(f"Vision request timed out (> {config.request_timeout_sec}s), skipped this frame.")
            except Exception as exc:
                log(f"Model request failed: {exc}")

        session.close()

    worker = threading.Thread(target=inference_worker, name="vision-worker", daemon=True)
    capture = threading.Thread(target=capture_worker, name="capture-worker", daemon=True)
    capture.start()
    worker.start()

    try:
        while True:
            start_ts = time.time()
            sampled_frame = None
            sampled_ts = 0.0
            with state_lock:
                if latest_capture_frame is not None:
                    sampled_frame = latest_capture_frame.copy()
                    sampled_ts = latest_capture_ts

            if sampled_frame is None:
                log("No captured frame available yet, skipping this sampling cycle.")
            else:
                snap = save_snapshot(config, sampled_frame)
                log(f"Captured frame: {snap}")
                with state_lock:
                    latest_frame = sampled_frame
                    latest_frame_ts = sampled_ts

            elapsed = time.time() - start_ts
            sleep_time = max(0.0, config.interval_sec - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        log("Interrupted by user.")
    finally:
        stop_event.set()
        capture.join(timeout=2.0)
        worker.join(timeout=2.0)
        camera.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
