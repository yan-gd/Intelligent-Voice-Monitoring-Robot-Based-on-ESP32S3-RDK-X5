# RDK X5 人脸追踪

使用 USB 摄像头 + OpenCV 检测人脸，通过 RDK X5 40Pin 上的 PWM6/PWM7 控制两个舵机。

## 接线

- PWM6: 物理 BOARD 32，用于抬头/低头舵机。
- PWM7: 物理 BOARD 33，用于水平旋转舵机。
- 舵机电源建议使用独立 5V 电源，RDK X5 与外部舵机电源必须共地。
- 不建议直接用 40Pin 的 5V 给大扭矩舵机供电，启动电流可能导致板子重启。

## 运行

```bash
cd /home/sunrise/Desktop/tracking-face
python3 tracking_face.py
```

显示调试窗口。只有在桌面终端里运行时使用，SSH 或无图形显示环境不要加 `--show`：

```bash
python3 tracking_face.py --show
```

只测试摄像头和人脸检测，不输出舵机 PWM：

```bash
python3 tracking_face.py --dry-run --show
```

如果你当前还在其他项目的虚拟环境里，程序会自动补充 RDK 系统 Python 包路径来加载 `Hobot.GPIO`。也可以直接退出虚拟环境后运行：

```bash
deactivate
cd /home/sunrise/Desktop/tracking-face
python3 tracking_face.py
```

当前水平舵机方向已经按实际安装默认反向。如果之后更换结构，水平需要恢复正常方向：

```bash
python3 tracking_face.py --normal-pan
```

如果俯仰方向反了：

```bash
python3 tracking_face.py --invert-tilt
```

## 默认参数

- 摄像头：`/dev/video0`
- 分辨率：`640x360@30`
- 共享帧发布：默认写入 `/tmp/rdk_shared_camera/latest.jpg`，供 `Qwen-ai` 读取
- 人脸检测：优先使用 YuNet，模型文件位于 `models/face_detection_yunet_2023mar.onnx`
- PWM 频率：`50Hz`
- 舵机脉宽：`500us` 到 `2500us`
- PWM6/BOARD32：俯仰
- PWM7/BOARD33：水平，默认反向
- 平滑控制：默认启用人脸中心误差低通滤波，舵机由独立 50Hz 控制线程按梯形速度曲线连续运动

调试时先让云端驾驶监督服务停止或保持 inactive，避免两个程序同时抢占 `/dev/video0`。

## 与 Qwen-ai 共用摄像头

两个项目同时运行时，由本项目独占 USB 摄像头并发布共享帧，`Qwen-ai` 读取共享帧做云端识别。

推荐启动顺序：

```bash
cd /home/sunrise/Desktop/tracking-face
python3 tracking_face.py --show
```

然后启动或重启 `Qwen-ai`。`Qwen-ai/.env` 应保持：

```bash
CAMERA_SOURCE=shared_jpeg
SHARED_FRAME_PATH=/tmp/rdk_shared_camera/latest.jpg
```

## 跟踪调参

更平滑但响应慢一些：

```bash
python3 tracking_face.py --error-filter-alpha 0.28 --target-filter-alpha 0.55 --pan-max-speed 110 --tilt-max-speed 90 --pan-max-accel 360 --tilt-max-accel 300
```

响应快一些但可能更有机械冲击：

```bash
python3 tracking_face.py --error-filter-alpha 0.55 --target-filter-alpha 0.9 --pan-max-speed 220 --tilt-max-speed 170 --pan-max-accel 760 --tilt-max-accel 620
```

参数含义：

- `--error-filter-alpha`: 人脸中心误差的一阶低通滤波系数，越小越平滑。
- `--target-filter-alpha`: 目标舵机角度滤波系数，越小目标越稳。
- `--detection-hz`: 人脸检测最高频率，默认 `10`。如果 CPU 高、舵机卡顿，先降到 `6` 或 `8`。
- `--pan-max-speed`: 水平舵机最大速度，单位是度/秒。
- `--tilt-max-speed`: 俯仰舵机最大速度，单位是度/秒。
- `--pan-max-accel`: 水平舵机最大加速度，单位是度/秒平方。
- `--tilt-max-accel`: 俯仰舵机最大加速度，单位是度/秒平方。
- `--servo-update-hz`: 舵机运动控制线程频率，默认 `50`。

如果出现每秒明显卡顿，通常是人脸检测把 CPU 跑满。先试：

```bash
python3 tracking_face.py --show --detection-hz 6
```
