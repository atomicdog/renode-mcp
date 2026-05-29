# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.5.0,<2",
# ]
# ///
"""
renode-mcp — MCP server for controlling Renode emulations.

Provides tools for launching a headless Renode instance, loading .resc scripts,
controlling emulation, reading/writing system-bus memory, and capturing UART output.

Communication with Renode uses the telnet monitor interface (--port flag).
"""

import json
import logging
import os
import re
import sys

from mcp.server.fastmcp import FastMCP

from config import (
    AVAILABLE_SCRIPTS,
    RENODE_BIN,
    RENODE_DIR,
    RENODE_TELNET_PORT,
)
from renode_client import RenodeClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("renode-mcp")

mcp = FastMCP("renode-mcp")

# Single global client instance
_client: RenodeClient | None = None


def _get_client() -> RenodeClient:
    """Return the active client or raise."""
    if _client is None or not _client.is_running():
        raise RuntimeError(
            "Renode is not running. Call renode_start first."
        )
    return _client


# ── Lifecycle tools ────────────────────────────────────────────────────


@mcp.tool()
def renode_start(script: str | None = None) -> str:
    """Start a headless Renode instance and optionally load a script.

    Requires the RENODE_PATH environment variable to point at a built Renode
    source tree (one containing output/bin/Release/Renode.dll).

    Args:
        script: Name of a .resc script to load — either a known script name
                (from RENODE_SCRIPTS_DIR) or an absolute path to a .resc file.

    Returns:
        Status message with connection details.
    """
    global _client

    if not RENODE_BIN:
        return (
            "RENODE_PATH is not set. Point it at a built Renode source tree "
            "(the directory containing output/bin/Release/Renode.dll)."
        )

    if _client is not None and _client.is_running():
        return "Renode is already running. Call renode_stop first."

    _client = RenodeClient(
        renode_bin=RENODE_BIN,
        port=RENODE_TELNET_PORT,
        cwd=RENODE_DIR,
    )

    _client.start()

    result_parts = [f"Renode started on telnet port {_client.port}."]

    # Load script if requested
    if script:
        script_path = AVAILABLE_SCRIPTS.get(script, script)
        if not script_path.startswith("@"):
            script_path = f"@{script_path}"
        try:
            output = _client.execute(f"include {script_path}", timeout=30)
            result_parts.append(f"Loaded script: {script}")
            if output:
                result_parts.append(f"Output: {output}")
        except Exception as e:
            result_parts.append(f"Script load error: {e}")

    return "\n".join(result_parts)


@mcp.tool()
def renode_stop() -> str:
    """Stop the running Renode instance."""
    global _client

    if _client is None:
        return "Renode is not running."

    _client.stop()
    _client = None
    return "Renode stopped."


@mcp.tool()
def renode_status() -> str:
    """Check if Renode is running and list available machines.

    Returns:
        JSON with running status, port, machine list, and known scripts.
    """
    if _client is None or not _client.is_running():
        return json.dumps({"running": False})

    # Get machine list
    try:
        output = _client.execute("mach", timeout=5)
    except Exception:
        output = ""

    return json.dumps({
        "running": True,
        "port": _client.port,
        "machines_output": output,
        "available_scripts": list(AVAILABLE_SCRIPTS.keys()),
    }, indent=2)


# ── Monitor command tools ──────────────────────────────────────────────


@mcp.tool()
def renode_execute(
    command: str,
    machine: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Execute an arbitrary Renode monitor command.

    This is the escape hatch for any command not covered by a dedicated tool.

    Args:
        command: Renode monitor command (e.g., "cpu PC", "logLevel 3").
        machine: Optional machine name to scope the command to. If provided,
                 'mach set "name"' is sent first.
        timeout: Seconds to wait for the command to complete (default 30).
                 Use higher values for python snippets or long operations.

    Returns:
        The command's text output.
    """
    client = _get_client()

    if machine:
        client.execute(f'mach set "{machine}"', timeout=5)

    return client.execute(command, timeout=timeout)


@mcp.tool()
def renode_load_script(
    script: str,
    variables: dict[str, str] | None = None,
) -> str:
    """Load a .resc script, optionally setting variables first.

    Args:
        script: Known script name (from RENODE_SCRIPTS_DIR) or an absolute path.
        variables: Optional dict of variable assignments to set before loading
                   (e.g., {"$fw": "@/path/to/firmware.bin"}). A leading '$' is
                   added automatically if omitted.

    Returns:
        Script output.
    """
    client = _get_client()

    # Set variables
    if variables:
        for var, value in variables.items():
            # Ensure variable starts with $
            if not var.startswith("$"):
                var = f"${var}"
            client.execute(f"{var}={value}", timeout=5)

    # Resolve script name
    script_path = AVAILABLE_SCRIPTS.get(script, script)
    if not script_path.startswith("@"):
        script_path = f"@{script_path}"

    return client.execute(f"include {script_path}", timeout=30)


@mcp.tool()
def renode_run_macro(macro_name: str, timeout: float = 30.0) -> str:
    """Execute a Renode macro defined in the loaded script.

    Macros are defined in .resc scripts and invoked with `runMacro $name`. Use
    renode_status / renode_execute to inspect what the currently loaded script
    provides.

    Args:
        macro_name: Macro name (a leading '$' is stripped if provided).
        timeout: Seconds to wait (default 30). Raise it for long-running macros.

    Returns:
        Macro output text.
    """
    client = _get_client()

    # Strip leading $ if caller included it
    macro_name = macro_name.lstrip("$")

    return client.execute(f"runMacro ${macro_name}", timeout=timeout)


# ── Emulation control tools ────────────────────────────────────────────


@mcp.tool()
def renode_start_emulation() -> str:
    """Start (resume) the emulation."""
    client = _get_client()
    return client.execute("start", timeout=10)


@mcp.tool()
def renode_pause_emulation() -> str:
    """Pause the emulation."""
    client = _get_client()
    return client.execute("pause", timeout=10)


@mcp.tool()
def renode_run_for(seconds: str) -> str:
    """Run the emulation for a specific duration of virtual time, then pause.

    This automatically starts the emulation, runs for the given duration,
    and pauses. Useful for stepping through boot sequences.

    Args:
        seconds: Virtual time duration as a string (e.g., "0.065", "0.3", "1.0").

    Returns:
        Confirmation.
    """
    client = _get_client()
    # RunFor can take a while for long durations
    timeout = max(30, float(seconds) * 100)
    return client.execute(
        f'emulation RunFor "{seconds}"', timeout=timeout
    )


# ── State inspection tools ─────────────────────────────────────────────


@mcp.tool()
def renode_read_memory(
    address: str,
    size: int = 4,
    count: int = 1,
    machine: str | None = None,
) -> str:
    """Read memory from the system bus.

    Args:
        address: Hex address (e.g., "0x20001000").
        size: Bytes per read — 1 (Byte), 2 (Word), or 4 (DoubleWord).
        count: Number of consecutive reads (each advanced by 'size' bytes).
        machine: Machine name. If None, uses the currently selected machine.

    Returns:
        JSON with address and value(s).
    """
    client = _get_client()
    if machine:
        client.execute(f'mach set "{machine}"', timeout=5)

    read_cmd = {1: "ReadByte", 2: "ReadWord", 4: "ReadDoubleWord"}.get(size)
    if read_cmd is None:
        return f"Invalid size {size}. Must be 1, 2, or 4."

    addr = int(address, 0)
    results = []
    for i in range(count):
        a = addr + i * size
        output = client.execute(f"sysbus {read_cmd} 0x{a:08X}", timeout=5)
        results.append({"address": f"0x{a:08X}", "value": output.strip()})

    if count == 1:
        return json.dumps(results[0], indent=2)
    return json.dumps(results, indent=2)


@mcp.tool()
def renode_write_memory(
    address: str,
    value: int,
    size: int = 4,
    machine: str | None = None,
) -> str:
    """Write a value to the system bus.

    Args:
        address: Hex address (e.g., "0x20001000").
        value: Integer value to write.
        size: Bytes to write — 1 (Byte), 2 (Word), or 4 (DoubleWord).
        machine: Machine name. If None, uses the currently selected machine.

    Returns:
        Confirmation.
    """
    client = _get_client()
    if machine:
        client.execute(f'mach set "{machine}"', timeout=5)

    write_cmd = {1: "WriteByte", 2: "WriteWord", 4: "WriteDoubleWord"}.get(size)
    if write_cmd is None:
        return f"Invalid size {size}. Must be 1, 2, or 4."

    addr = int(address, 0)
    output = client.execute(
        f"sysbus {write_cmd} 0x{addr:08X} 0x{value:X}", timeout=5
    )
    return output if output else f"Wrote 0x{value:X} to 0x{addr:08X}"


# ── Log file path (set when logFile is enabled) ──────────────────────

_log_file_path: str | None = None


@mcp.tool()
def renode_enable_log(path: str = "/tmp/renode_mcp.log") -> str:
    """Enable Renode log output to a file and return the path.

    Call this once after loading a script to capture all UART output and
    peripheral activity.  Use renode_get_uart_output to read the captured text.

    Args:
        path: Absolute path for the log file (default: /tmp/renode_mcp.log).

    Returns:
        Confirmation with the log file path.
    """
    global _log_file_path
    client = _get_client()
    client.execute(f"logFile @{path} true", timeout=5)
    _log_file_path = path
    return f"Logging enabled to {path}"


@mcp.tool()
def renode_get_uart_output(
    machine: str | None = None,
    tail: int = 50,
) -> str:
    """Read recent UART console output from the Renode log file.

    Requires renode_enable_log to have been called first.

    This parser targets UART output as emitted by Infineon XMC USIC channels,
    which appear as INFO-level log entries (e.g. "usic0ch1: [host: ...] text").
    Adjust the regex below for other UART peripherals.

    Args:
        machine: Reserved for filtering by machine name (the Renode log line
                 does not carry the machine name, so this is currently a no-op).
        tail: Maximum number of UART lines to return (default 50, from the end).

    Returns:
        The extracted UART text, one line per message.
    """
    if _log_file_path is None:
        return "Error: call renode_enable_log first."

    try:
        with open(_log_file_path, "r", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"Error: log file {_log_file_path} not found."

    # UART output lines look like:
    #   HH:MM:SS.FFFF [INFO] usic0ch1: [host: ...|virt: ...] message text
    uart_re = re.compile(
        r"\[INFO\]\s+(usic\dch[01]):\s+"
        r"\[host:.*?\|virt:.*?\]\s*(.*)"
    )

    uart_lines = []
    for line in lines:
        m = uart_re.search(line)
        if not m:
            continue
        text = m.group(2)
        # Skip empty lines and KickTx noise
        if not text or "KickTx" in line:
            continue
        uart_lines.append(text)

    # Return the last N lines
    result = uart_lines[-tail:] if len(uart_lines) > tail else uart_lines
    return "\n".join(result) if result else "(no UART output captured)"


# ── Entry point ────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
