#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import serial


TRACKING_DIR = Path("/home/sunrise/Desktop/tracking-face")
QWEN_DIR = Path("/home/sunrise/Desktop/Qwen-ai")
TRACKING_SCRIPT = TRACKING_DIR / "tracking_face.py"
QWEN_SCRIPT = QWEN_DIR / "driver_safety_supervisor.py"
QWEN_VENV_ACTIVATE = QWEN_DIR / ".venv" / "bin" / "activate"
SHARED_FRAME = Path("/tmp/rdk_shared_camera/latest.jpg")


class ScriptManager:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.tracking_proc: Optional[subprocess.Popen] = None
        self.qwen_proc: Optional[subprocess.Popen] = None

    def start_all(self) -> None:
        self.start_tracking()
        self.wait_for_shared_frame()
        self.start_qwen()

    def stop_all(self) -> None:
        self.stop_qwen()
        self.stop_tracking()

    def start_tracking(self) -> None:
        if self.is_running(self.tracking_proc):
            log("tracking-face already running.")
            return
        if self.external_script_running("tracking_face.py"):
            log("tracking-face is already running outside uart-control.")
            return

        cmd = [
            sys.executable,
            str(TRACKING_SCRIPT),
            "--publish-frame",
            str(SHARED_FRAME),
        ]
        if self.args.show_tracking:
            cmd.append("--show")

        log(f"Starting tracking-face: {' '.join(cmd)}")
        self.tracking_proc = subprocess.Popen(
            cmd,
            cwd=str(TRACKING_DIR),
            start_new_session=True,
        )

    def start_qwen(self) -> None:
        if self.is_running(self.qwen_proc):
            log("Qwen-ai already running.")
            return
        if self.external_script_running("driver_safety_supervisor.py"):
            log("Qwen-ai is already running outside uart-control.")
            return

        env = os.environ.copy()
        env["CAMERA_SOURCE"] = "shared_jpeg"
        env["SHARED_FRAME_PATH"] = str(SHARED_FRAME)

        if not QWEN_VENV_ACTIVATE.exists():
            raise RuntimeError(f"Qwen-ai virtualenv activate script not found: {QWEN_VENV_ACTIVATE}")

        command = f"source '{QWEN_VENV_ACTIVATE}' && python '{QWEN_SCRIPT}'"
        cmd = ["bash", "-lc", command]
        log(f"Starting Qwen-ai: {' '.join(cmd)}")
        self.qwen_proc = subprocess.Popen(
            cmd,
            cwd=str(QWEN_DIR),
            env=env,
            start_new_session=True,
        )

    def stop_tracking(self) -> None:
        self.tracking_proc = self.stop_process(self.tracking_proc, "tracking-face")
        self.stop_external_script("tracking_face.py", "tracking-face")

    def stop_qwen(self) -> None:
        self.qwen_proc = self.stop_process(self.qwen_proc, "Qwen-ai")
        self.stop_external_script("driver_safety_supervisor.py", "Qwen-ai")

    def wait_for_shared_frame(self) -> None:
        deadline = time.time() + self.args.shared_frame_timeout
        while time.time() < deadline:
            if SHARED_FRAME.exists() and SHARED_FRAME.stat().st_size > 0:
                return
            time.sleep(0.1)
        log(f"Shared frame not ready after {self.args.shared_frame_timeout:.1f}s; starting Qwen-ai anyway.")

    def reap_exited(self) -> None:
        if self.tracking_proc is not None and self.tracking_proc.poll() is not None:
            log(f"tracking-face exited with code {self.tracking_proc.returncode}.")
            self.tracking_proc = None
        if self.qwen_proc is not None and self.qwen_proc.poll() is not None:
            log(f"Qwen-ai exited with code {self.qwen_proc.returncode}.")
            self.qwen_proc = None

    @staticmethod
    def is_running(proc: Optional[subprocess.Popen]) -> bool:
        return proc is not None and proc.poll() is None

    @staticmethod
    def external_script_running(script_name: str) -> bool:
        result = subprocess.run(
            ["pgrep", "-f", script_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def stop_external_script(self, script_name: str, name: str) -> None:
        result = subprocess.run(
            ["pgrep", "-f", script_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return

        own_pid = os.getpid()
        managed_pids = {
            proc.pid
            for proc in [self.tracking_proc, self.qwen_proc]
            if proc is not None
        }
        pids = [
            int(line)
            for line in result.stdout.splitlines()
            if line.strip().isdigit()
            and int(line) != own_pid
            and int(line) not in managed_pids
        ]
        if not pids:
            return

        log(f"Stopping external {name} process(es): {pids}")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.time() + self.args.stop_timeout
        while time.time() < deadline:
            if not any(process_exists(pid) for pid in pids):
                return
            time.sleep(0.1)
        for pid in pids:
            if process_exists(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def stop_process(self, proc: Optional[subprocess.Popen], name: str) -> Optional[subprocess.Popen]:
        if not self.is_running(proc):
            log(f"{name} is not running.")
            return None

        log(f"Stopping {name}...")
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=self.args.stop_timeout)
        except subprocess.TimeoutExpired:
            log(f"{name} did not stop in time, killing.")
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
        return None


def parse_command(raw: bytes) -> Optional[int]:
    for value in raw:
        if value in {0x01, 0x02, 0x03}:
            return value

    text = raw.decode("ascii", errors="ignore").strip().lower()
    if not text:
        return None
    if text.startswith("0x"):
        text = text[2:]
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return value if value in {0x01, 0x02, 0x03} else None


def log(message: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UART command controller for tracking-face and Qwen-ai.")
    parser.add_argument("--port", default="/dev/ttyS1", help="UART device for BOARD pin 8 TXD and pin 10 RXD.")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=0.2)
    parser.add_argument("--stop-timeout", type=float, default=5.0)
    parser.add_argument("--shared-frame-timeout", type=float, default=5.0)
    parser.add_argument("--show-tracking", action="store_true", help="Start tracking-face with preview window.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manager = ScriptManager(args)
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log(f"Opening UART {args.port} @ {args.baudrate} baud.")
    try:
        uart = serial.Serial(args.port, args.baudrate, timeout=args.timeout)
    except Exception as exc:
        print(f"Failed to open UART {args.port}: {exc}", file=sys.stderr)
        return 1

    log("Ready. Commands: 0x01=start tracking then Qwen-ai, 0x02=stop both, 0x03=stop Qwen-ai only.")
    try:
        while running:
            manager.reap_exited()
            raw = uart.readline()
            if not raw:
                raw = uart.read(uart.in_waiting or 1)
            command = parse_command(raw)
            if command is None:
                continue

            log(f"Received command 0x{command:02X}.")
            if command == 0x01:
                manager.start_all()
            elif command == 0x02:
                manager.stop_all()
            elif command == 0x03:
                manager.stop_qwen()
    finally:
        uart.close()
        manager.stop_all()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
