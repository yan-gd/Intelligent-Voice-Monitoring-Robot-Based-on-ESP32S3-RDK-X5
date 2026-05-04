# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

XiaoZhi AI Chatbot (小智 AI 聊天机器人) is an ESP32-based voice interaction device firmware. It captures voice input, sends it to cloud AI services (Qwen / DeepSeek), plays back streaming TTS responses, and controls hardware via the MCP protocol. Supports ESP32, ESP32-C3, ESP32-C5, ESP32-C6, ESP32-S3, and ESP32-P4 platforms.

## Build / Develop

- **IDE**: VS Code or Cursor with ESP-IDF plugin, SDK v5.4+. Linux preferred over Windows for build speed.
- **Set target chip** (first time or when switching):
  ```bash
  idf.py set-target esp32s3   # or esp32, esp32c3, esp32c6, esp32p4
  ```
- **Configure Kconfig**: `idf.py menuconfig` — navigate to `Xiaozhi Assistant` to select board type, language, flash assets, etc.
- **Build**: `idf.py build`
- **Flash + monitor**: `idf.py flash monitor`
- **Full clean rebuild**: `idf.py fullclean && idf.py build`
- **Build release firmware for a board**: `python scripts/release.py <board-directory-name>` (uses the board's `config.json` for target and sdkconfig overrides)
- **Code style**: Google C++ style via `.clang-format`. Format before committing:
  ```bash
  clang-format -i path/to/file.cc
  # or whole project:
  find main -iname *.h -o -iname *.cc | xargs clang-format -i
  ```

## Architecture

### Entry Point and Main Loop

[main.cc](main/main.cc) — `app_main()` initializes NVS, creates the `Application` singleton, calls `Initialize()` then `Run()`. `Run()` is an event-driven FreeRTOS loop that never returns, handling events via `EventGroup` bits (`MAIN_EVENT_*`).

### Application Singleton

[application.h](main/application.h) / [application.cc](main/application.cc) — Central coordinator. Owns the `Protocol`, `AudioService`, `Ota`, and `DeviceStateMachine`. All long-running operations (network connect, OTA, activation) run in separate FreeRTOS tasks. Thread safety: `Schedule()` allows other tasks/ISRs to enqueue callbacks that execute on the main event loop.

### Device State Machine

[device_state_machine.h](main/device_state_machine.h) / [device_state_machine.cc](main/device_state_machine.cc) — Strict state transition validation. States: `Unknown → Starting → Idle → Connecting → Listening → Speaking`, plus `WifiConfiguring`, `Upgrading`, `Activating`, `AudioTesting`, `FatalError`. Observer pattern via `StateCallback` for components to react to transitions.

### Audio Pipeline

[audio/audio_service.h](main/audio/audio_service.h) — Two data flows run across three FreeRTOS tasks:
1. **MIC → Processors → Encode Queue → Opus Encoder → Send Queue → Server** (input task + codec task)
2. **Server → Decode Queue → Opus Decoder → Playback Queue → Speaker** (output task + codec task)

Key pieces:
- **AudioCodec** ([audio/audio_codec.h](main/audio/audio_codec.h)): Abstract interface for hardware codecs. Implementations in `audio/codecs/` (ES8311, ES8374, ES8388, ES8389, etc.)
- **AudioProcessor** ([audio/audio_processor.h](main/audio/audio_processor.h)): Preprocessing (AEC, noise suppression). `afe_audio_processor.cc` for ESP-SR AFE; `no_audio_processor.cc` passthrough.
- **WakeWord** ([audio/wake_word.h](main/audio/wake_word.h)): Offline wake word detection. ESP32-S3/P4 use `afe_wake_word.cc` (MultiNet); other chips use `esp_wake_word.cc`.
- OPUS codec at 16kHz mono, 60ms frame duration by default.

### Communication Protocols

[protocols/protocol.h](main/protocols/protocol.h) — Abstract `Protocol` base class with callbacks for audio/json data, channel state, and network events. Two implementations:
- `WebsocketProtocol` — primary protocol, sends/receives binary protocol v2/v3 + JSON
- `MqttProtocol` — MQTT for signaling + UDP for audio transport

Binary framing: `BinaryProtocol2` (v2) has 12-byte header (version, type, reserved, timestamp, payload_size); `BinaryProtocol3` (v3) has 4-byte header (type, reserved, payload_size).

### MCP Server (Device-Side)

[mcp_server.h](main/mcp_server.h) / [mcp_server.cc](main/mcp_server.cc) — Implements the Model Context Protocol on-device. `McpServer` singleton manages a list of `McpTool` objects. Each tool has a name, description, typed `PropertyList`, and a callback returning `ReturnValue` (bool/int/string/cJSON/ImageContent). Common tools (volume, LED, servo, GPIO) are registered in `AddCommonTools()`; user-only tools in `AddUserOnlyTools()`. Messages are JSON-RPC style parsed via cJSON.

### Board Abstraction

`boards/common/board.cc` — Abstract `Board` base class. Hierarchy:
- `WifiBoard` — Wi-Fi connected boards (most common)
- `Ml307Board` — 4G Cat.1 cellular boards
- `DualNetworkBoard` — Wi-Fi + 4G fallback

Each board in `boards/<board-name>/` provides:
- `config.h` — GPIO pin mappings, display dimensions, audio codec configuration
- `<board>.cc` — Implements `GetAudioCodec()`, `GetDisplay()`, `GetBacklight()`, button wiring, MCP tools
- `config.json` — target chip and sdkconfig overrides for release builds

Boards register themselves via the `DECLARE_BOARD(ClassName)` macro. Board type is selected at compile time via Kconfig (`CONFIG_BOARD_TYPE_*`) in [main/Kconfig.projbuild](main/Kconfig.projbuild) and mapped to directory names in [main/CMakeLists.txt](main/CMakeLists.txt).

### Display Abstraction

`display/display.h` — Abstract `Display` base class. Implementations:
- `LcdDisplay` / `SpiLcdDisplay` — SPI LCD panels (ST7789, ILI9341, etc.)
- `OledDisplay` — SSD1306/SH1106 I2C OLEDs
- `LvglDisplay` — LVGL-based displays with emoji, GIF, and JPEG support
- `EmoteDisplay` — animation-based expression display

### OTA Updates

[ota.cc](main/ota.cc) — Downloads firmware from configured OTA URL, writes to OTA partition, validates, and reboots. Supports progress callbacks and activation code pairing during setup.

### Settings

[settings.cc](main/settings.cc) — NVS-backed key-value store with namespace support. Used for persistent config (UUID, Wi-Fi credentials, activation state, audio params).

### Assets System

[assets.cc](main/assets.cc) — Reads from the `assets` flash partition (mmap-backed). Stores fonts (builtin text + icon fonts), emoji collections, wake word models, and localized sound files. Assets can be default (built from config), custom (user-provided URL/file), or expression-based (for emote displays).

### Localization

Language-specific strings defined in `assets/locales/<lang-Code>/language.json`. A Python script ([scripts/gen_lang.py](scripts/gen_lang.py)) generates a C header at build time. Audio prompts are per-language `.ogg` files; missing files fall back to `en-US`.

### Partition Tables

Version 2 partition layout in `partitions/v2/` — includes dedicated `assets` partition. v1 and v2 are incompatible (no OTA cross-grade). Partition size variants: 4m, 8m, 16m.

## Key Documentation

- [docs/custom-board.md](docs/custom-board.md) — Step-by-step guide for adding a new hardware board
- [docs/websocket.md](docs/websocket.md) — WebSocket communication protocol details
- [docs/mqtt-udp.md](docs/mqtt-udp.md) — MQTT+UDP hybrid protocol
- [docs/mcp-protocol.md](docs/mcp-protocol.md) — Device-side MCP implementation
- [docs/mcp-usage.md](docs/mcp-usage.md) — IoT control via MCP
- [docs/code_style.md](docs/code_style.md) — Code formatting guide
