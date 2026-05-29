# renode-mcp

MCP server for controlling headless Renode emulations over the telnet monitor interface.

## How It Works

The server launches a headless Renode process and communicates via its telnet monitor
interface. It exposes 13 MCP tools for lifecycle management, script loading, emulation
control, memory inspection, and UART capture.

## Running

```bash
# As MCP server (stdio transport, used by Claude Code)
python3 renode_mcp.py

# Environment variables:
#   RENODE_PATH        - Built Renode source tree (contains output/bin/Release/Renode.dll). Required.
#   RENODE_TELNET_PORT - Telnet port for Renode monitor (default: 1234)
#   RENODE_SCRIPTS_DIR - Optional directory of .resc scripts, exposed by filename stem
```

## Tools

**Lifecycle:** `renode_start`, `renode_stop`, `renode_status`
**Commands:** `renode_execute`, `renode_load_script`, `renode_run_macro`
**Emulation:** `renode_start_emulation`, `renode_pause_emulation`, `renode_run_for`
**Inspection:** `renode_read_memory`, `renode_write_memory`
**Logging:** `renode_enable_log`, `renode_get_uart_output`

## Typical Workflow

```
renode_start(script="my_board")      # Launch + load a .resc script
renode_enable_log()                  # Enable UART capture
renode_run_for("0.3")                # Run 300ms of virtual time, then pause
renode_get_uart_output(tail=20)      # Read last 20 UART lines
renode_execute("cpu PC", machine="cpu0")
renode_read_memory("0x20001000", size=4)
renode_stop()
```

## Scripts

If `RENODE_SCRIPTS_DIR` is set, every `*.resc` file under it is exposed to
`renode_start` / `renode_load_script` by its filename stem (e.g. `my_board` for
`my_board.resc`). Scripts can also be referenced by absolute path.

## Dependencies

- Python 3.10+
- `mcp>=1.5.0`
- A built Renode at `RENODE_PATH`
