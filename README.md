# renode-mcp

MCP (Model Context Protocol) server for controlling [Renode](https://renode.io) emulations.

## Overview

This server lets Claude Code and other MCP clients start, control, and inspect a headless
Renode instance: launch it, load `.resc` scripts, run/pause emulation, read and write
system-bus memory, and capture UART output — all over Renode's telnet monitor interface.

It is firmware- and platform-agnostic: point it at your built Renode and (optionally) a
directory of `.resc` scripts, and drive whatever machines those scripts define.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Build Renode (if you haven't already):
   ```bash
   cd /path/to/renode && ./build.sh --no-gui
   ```

3. Register in your MCP client (see `mcp-config.json` for an example) and set the
   environment variables below.

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `RENODE_PATH` | yes | Path to a built Renode source tree (contains `output/bin/Release/Renode.dll`). |
| `RENODE_TELNET_PORT` | no | Telnet port for the Renode monitor (default `1234`). |
| `RENODE_SCRIPTS_DIR` | no | Directory of `.resc` scripts. Each is exposed by its filename stem; scripts can also be referenced by absolute path. |

## Available Tools

| Tool | Description |
|------|-------------|
| `renode_start` | Launch Renode and optionally load a `.resc` script |
| `renode_stop` | Stop the Renode instance |
| `renode_status` | Check status, list machines and known scripts |
| `renode_execute` | Execute an arbitrary monitor command |
| `renode_load_script` | Load a `.resc` script with optional variable overrides |
| `renode_run_macro` | Run a macro defined in the loaded script |
| `renode_start_emulation` | Start (resume) emulation |
| `renode_pause_emulation` | Pause emulation |
| `renode_run_for` | Run for a specific virtual time duration |
| `renode_read_memory` | Read bytes from the system bus |
| `renode_write_memory` | Write bytes to the system bus |
| `renode_enable_log` | Enable log file capture for UART output |
| `renode_get_uart_output` | Read recent UART console output from the log |

## Architecture

```
MCP Client  <--stdio-->  renode_mcp.py  <--telnet-->  Renode (headless)
```

The server manages a single Renode process, launched with `--port <N> --disable-xwt --plain`.
Commands are sent as text over a TCP telnet socket to the Renode monitor console.

## License

MIT — see [LICENSE](LICENSE).
