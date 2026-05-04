# Project Guidelines

## Code Style
- Follow the repository formatting and keep diffs focused on the requested task.
- Use the existing C/C++ patterns in `main/` and board-specific layout in `main/boards`.
- Respect the project style guide; run formatting when touching C/C++ files.

## Architecture
- Core firmware lives in `main/`.
- Board adaptations live in `main/boards/<board-name>/` with `config.h` and `config.json`.
- Third-party and managed dependencies are in `managed_components/`.
- Partition definitions are in `partitions/` (v2 layouts are in `partitions/v2/`).

## Build and Test
- Preferred build flow:
  - `idf.py build`
- Clean build:
  - `idf.py fullclean`
  - `idf.py build`
- Flash device:
  - `idf.py -p <PORT> flash`
- Build/package board releases:
  - `python scripts/release.py --list-boards`
  - `python scripts/release.py <board-name>`

## Conventions
- Do not overwrite existing board definitions for custom hardware; create a new board directory and unique build name.
- Keep board identity unique to avoid OTA upgrade collisions.
- Verify partition table/flash-size settings match hardware before flashing.
- v2 partition layout is not OTA-upgrade-compatible with v1; upgrades from v1 require full flash.
- If a task depends on protocol behavior, check docs before changing packet/message formats.

## Key Docs
- Project overview and version notes: `README_zh.md`
- Custom board workflow: `docs/custom-board.md`
- Code style and formatting: `docs/code_style.md`
- WebSocket protocol: `docs/websocket.md`
- MQTT + UDP protocol: `docs/mqtt-udp.md`
- MCP usage: `docs/mcp-usage.md`
- MCP protocol details: `docs/mcp-protocol.md`
- Partition layout notes: `partitions/v2/README.md`
