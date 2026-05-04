# UART 控制脚本

监听 RDK X5 40Pin 的 UART 引脚，根据串口命令启动或停止两个项目。

## 接线

- BOARD 8: `UART_TXD`
- BOARD 10: `UART_RXD`
- GND: 任意 GND 引脚，例如 BOARD 6/9/14/20/25/30/34/39

默认串口设备使用 `/dev/ttyS1`，波特率 `115200`。如果实际映射不同，可用 `--port` 修改。

## 命令

- `0x01`: 先启动 `tracking-face`，再启动 `Qwen-ai`
- `0x02`: 关闭 `tracking-face` 和 `Qwen-ai`
- `0x03`: 只关闭 `Qwen-ai`，保持人脸追踪运行

脚本同时兼容发送原始字节 `0x01`，以及文本形式 `01`、`0x01`。

## 运行

```bash
cd /home/sunrise/Desktop/uart-control
python3 uart_control.py
```

如果要启动人脸追踪预览窗口：

```bash
python3 uart_control.py --show-tracking
```

指定串口：

```bash
python3 uart_control.py --port /dev/ttyS1 --baudrate 115200
```

## 开机自启

项目内已提供 `uart-control.service`。安装到 systemd：

```bash
sudo cp /home/sunrise/Desktop/uart-control/uart-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable uart-control.service
sudo systemctl start uart-control.service
```

查看状态：

```bash
systemctl status uart-control.service
```

查看实时日志：

```bash
journalctl -u uart-control.service -f
```

停止或取消开机自启：

```bash
sudo systemctl stop uart-control.service
sudo systemctl disable uart-control.service
```

## 说明

- `0x01` 会等待人脸追踪发布 `/tmp/rdk_shared_camera/latest.jpg`，然后再启动 `Qwen-ai`。
- 启动 `Qwen-ai` 时会先执行 `source /home/sunrise/Desktop/Qwen-ai/.venv/bin/activate`，再运行主脚本。
- `Qwen-ai` 会被强制设置为 `CAMERA_SOURCE=shared_jpeg`，从共享帧读取画面，不直接抢占摄像头。
- 停止脚本自身时，会自动停止它启动的两个子进程。
