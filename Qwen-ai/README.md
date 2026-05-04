# 驾驶员安全监督机器人

## 功能
- 使用 USB 摄像头采集画面
- 按固定周期抓拍（默认 1 秒一帧）
- 调用阿里云 DashScope 视觉模型（默认 `qwen3.6-plus`）做危险行为分类
- 仅保留最新帧推理，不排队；支持请求超时和最小推理间隔
- 检测到异常类别（愤怒、闭眼、玩手机、喝水）时，本地随机播放对应语音文件
- 若无异常，不播放语音

## 目录
- `driver_safety_supervisor.py`: 主程序
- `.env`: 环境变量配置
- `requirements.txt`: 依赖
- `tts/`: 本地语音目录（按类别分子目录）

## Ubuntu 部署（目标目录）
以下步骤在 Ubuntu 上执行，项目目录为 `/home/sunrise/Desktop/Qwen-ai`。

1. 进入目录并创建虚拟环境

```bash
cd /home/sunrise/Desktop/Qwen-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 配置环境变量

```bash
# 编辑 .env，填入 DASHSCOPE_API_KEY
```

3. 安装音频播放工具（至少安装一个）

```bash
sudo apt update
sudo apt install -y ffmpeg alsa-utils pulseaudio-utils
```

4. 手动启动调试

```bash
source .venv/bin/activate
python driver_safety_supervisor.py
```

## 与人脸追踪共用摄像头

人脸追踪程序需要高帧率闭环控制舵机，因此由 `tracking-face` 独占 `/dev/video0` 并发布共享帧：

```bash
cd /home/sunrise/Desktop/tracking-face
python3 tracking_face.py --show
```

本项目默认配置为 `CAMERA_SOURCE=shared_jpeg`，从 `/tmp/rdk_shared_camera/latest.jpg` 读取共享帧，不再直接打开 USB 摄像头。这样两个功能可以同时存在：

- `tracking-face`: 打开 USB 摄像头，做人脸追踪和舵机控制。
- `Qwen-ai`: 读取共享最新帧，做驾驶行为云端识别和语音告警。

如果需要让本项目单独运行并直接占用摄像头，把 `.env` 改为：

```bash
CAMERA_SOURCE=usb
```

## 说明
- 行为分类结果只使用固定类别：`normal | angry | eyes_closed | phone_use | drinking`。
- 本地语音目录默认是 `tts/`，需要存在子目录：`angry/`、`eyes_closed/`、`phone_use/`、`drinking/`。
- 若服务运行但无声音，先检查系统可用播放器：`ffplay`、`mpg123`、`mpv`、`cvlc`、`aplay`、`paplay`。
- 程序会将最新抓拍图保存为 `runtime/latest.jpg`。
- 默认从人脸追踪程序发布的共享帧读取画面；USB 直连模式下通过 OpenCV 读取 `/dev/video*` 设备。
- 已在 `lsusb` 中确认过的 USB 摄像头为 `1b3f:2002 Generalplus Technology Inc. 808 Camera`。它还需要在系统里显示为 `/dev/video0`、`/dev/video1` 等 V4L2 设备后，程序才能读取画面。
- 如果程序提示找不到 `/dev/video*`，先执行：`ls -l /dev/video*`、`v4l2-ctl --list-devices`、`dmesg | grep -iE 'uvc|video|1b3f|2002'`。
- 关键实时参数可通过 `.env` 调整：`CAMERA_SOURCE`、`SHARED_FRAME_PATH`、`CAMERA_INDEX`、`CAMERA_PROBE_MAX`、`CAMERA_WIDTH`、`CAMERA_HEIGHT`、`CAMERA_FPS`、`CAMERA_FOURCC`、`CAPTURE_INTERVAL_SEC`、`REQUEST_TIMEOUT_SEC`、`INFER_MIN_INTERVAL_SEC`、`THINKING_ENABLED`。
