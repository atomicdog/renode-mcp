"""
RenodeClient — manages a headless Renode process and communicates via telnet.

Renode is launched with `--port <N> --disable-xwt --plain` which exposes its
monitor console over a TCP socket using the telnet protocol.  This module
handles the telnet IAC negotiation, prompt detection, and command/response
serialization so callers can simply do:

    client = RenodeClient()
    client.start()
    print(client.execute("help"))
    client.stop()
"""

import logging
import os
import re
import socket
import subprocess
import threading
import time
from typing import Optional

from config import RENODE_BIN, RENODE_DIR, RENODE_TELNET_PORT

logger = logging.getLogger("renode-mcp")

# Telnet protocol constants (RFC 854)
IAC  = 0xFF
WILL = 0xFB
WONT = 0xFC
DO   = 0xFD
DONT = 0xFE
SB   = 0xFA  # subnegotiation begin
SE   = 0xF0  # subnegotiation end

# Renode sends these on connect (SocketServerProvider.cs:392-397):
#   IAC DO BINARY(0), IAC DO NAWS(31), IAC WILL ECHO(1),
#   IAC WILL SGA(3), IAC WONT LINEMODE(34)
# We respond: WONT for all DO requests, DO for WILL ECHO/SGA, DONT otherwise.

# Prompt regex: matches "(monitor) " or any "(machine-name) " prompt.
# Renode prepends \r before the prompt, so we match after optional \r/\n.
PROMPT_RE = re.compile(r"\([\w-]+\)\s*$")


class RenodeClient:
    """Manages a Renode process and communicates via its telnet monitor."""

    def __init__(
        self,
        renode_bin: str = RENODE_BIN,
        port: int = RENODE_TELNET_PORT,
        cwd: str = RENODE_DIR,
    ):
        self._renode_bin = renode_bin
        self._port = port
        self._cwd = cwd

        self._process: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None

        # Reader thread state
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_running = False
        self._response_text = ""
        self._prompt_event = threading.Event()

        # Serialize command execution
        self._lock = threading.Lock()

        # Telnet IAC state machine
        self._telnet_state = "normal"

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self, startup_commands: Optional[list[str]] = None) -> None:
        """Launch Renode and connect to its telnet monitor.

        Args:
            startup_commands: Optional list of -e commands to pass on the
                Renode command line (executed before telnet interaction).
        """
        if self._process is not None:
            raise RuntimeError("Renode is already running")

        cmd = [
            "dotnet", self._renode_bin,
            "--port", str(self._port),
            "--disable-xwt",
            "--plain",
        ]
        if startup_commands:
            for sc in startup_commands:
                cmd.extend(["-e", sc])

        logger.info("Launching Renode: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            cwd=self._cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for telnet port to become available
        connected = False
        for _ in range(20):  # 10 seconds max
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"Renode exited immediately with code {self._process.returncode}"
                )
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(("127.0.0.1", self._port))
                self._sock = sock
                connected = True
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.5)

        if not connected:
            self.stop()
            raise RuntimeError(
                f"Could not connect to Renode telnet on port {self._port}"
            )

        logger.info("Connected to Renode telnet on port %d", self._port)

        # Consume initial IAC negotiation and first prompt
        self._handle_initial_negotiation()

        # Start background reader
        self._reader_running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="renode-reader"
        )
        self._reader_thread.start()

        # Wait for the initial prompt
        self._prompt_event.clear()
        if not self._prompt_event.wait(timeout=10.0):
            logger.warning("Did not receive initial prompt within 10s")

    def stop(self) -> None:
        """Stop Renode and clean up."""
        self._reader_running = False

        if self._sock:
            try:
                self._sock.sendall(b"quit\n")
                time.sleep(0.3)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None

        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None

        logger.info("Renode stopped")

    def is_running(self) -> bool:
        """Check if the Renode process is alive and socket is connected."""
        if self._process is None or self._process.poll() is not None:
            return False
        if self._sock is None:
            return False
        return True

    @property
    def port(self) -> int:
        return self._port

    # ── Command execution ──────────────────────────────────────────────

    def execute(self, command: str, timeout: float = 30.0) -> str:
        """Send a monitor command and return the response text.

        Thread-safe — only one command executes at a time.

        Args:
            command: Renode monitor command (e.g., "cpu PC")
            timeout: Seconds to wait for the prompt after sending

        Returns:
            The text output between sending the command and the next prompt.

        Raises:
            RuntimeError: If Renode is not running
            TimeoutError: If no prompt appears within timeout
        """
        if not self.is_running():
            raise RuntimeError("Renode is not running")

        with self._lock:
            # Drain any stale data from the previous command
            self._prompt_event.clear()
            self._response_text = ""
            # Small settle time so the reader finishes writing old prompt bytes
            time.sleep(0.05)
            self._response_text = ""

            self._sock.sendall((command + "\n").encode("utf-8"))

            if not self._prompt_event.wait(timeout=timeout):
                raise TimeoutError(
                    f"No prompt after {timeout}s for command: {command!r}"
                )

            # Small settle time for any trailing bytes after the prompt
            time.sleep(0.02)
            result = self._response_text

            # Split into lines and clean up
            lines = result.split("\n")

            # Remove the echoed command. Renode echoes exactly what we sent,
            # so match the line that is just the command text (not a substring).
            cleaned = []
            echo_stripped = False
            cmd_text = command.strip()
            for line in lines:
                stripped = line.strip()
                if not echo_stripped and stripped == cmd_text:
                    echo_stripped = True
                    continue
                # Remove prompt-only lines (e.g., "(monitor) " or "(machine) ")
                if PROMPT_RE.search(stripped) and len(stripped) < 30:
                    continue
                # Skip empty lines at start
                if not cleaned and not stripped:
                    continue
                cleaned.append(line)

            return "\n".join(cleaned).strip()

    # ── Telnet IAC handling ────────────────────────────────────────────

    def _handle_initial_negotiation(self) -> None:
        """Consume the initial telnet IAC bytes sent by Renode on connect.

        Renode sends 15 bytes of negotiation:
            FF FD 00  (DO BINARY)     -> we reply WONT BINARY
            FF FD 1F  (DO NAWS)       -> we reply WONT NAWS
            FF FB 01  (WILL ECHO)     -> we reply DO ECHO
            FF FB 03  (WILL SGA)      -> we reply DO SGA
            FF FC 22  (WONT LINEMODE) -> no response needed
        """
        self._sock.settimeout(3.0)
        try:
            data = self._sock.recv(256)
        except socket.timeout:
            logger.warning("No initial telnet data received")
            return
        finally:
            self._sock.settimeout(None)

        responses = bytearray()
        i = 0
        while i < len(data):
            if data[i] == IAC and i + 2 < len(data):
                cmd = data[i + 1]
                opt = data[i + 2]
                if cmd == DO:
                    # Server asks us to enable something — decline
                    responses.extend([IAC, WONT, opt])
                elif cmd == WILL:
                    # Server offers a capability — accept ECHO and SGA
                    if opt in (1, 3):  # ECHO, SGA
                        responses.extend([IAC, DO, opt])
                    else:
                        responses.extend([IAC, DONT, opt])
                # WONT / DONT need no reply
                i += 3
            else:
                i += 1

        if responses:
            self._sock.sendall(bytes(responses))

    def _strip_iac(self, data: bytes) -> str:
        """Process raw socket data through the telnet state machine.

        Returns the clean text with all IAC sequences removed.
        """
        text = []
        i = 0
        while i < len(data):
            b = data[i]

            if self._telnet_state == "normal":
                if b == IAC:
                    self._telnet_state = "iac"
                elif b == 0x0D:
                    pass  # drop \r (Renode sends \r\n)
                elif b < 128:
                    text.append(chr(b))
            elif self._telnet_state == "iac":
                if b in (WILL, WONT, DO, DONT):
                    self._telnet_state = "negotiation"
                elif b == SB:
                    self._telnet_state = "subneg"
                elif b == IAC:
                    text.append(chr(0xFF))
                    self._telnet_state = "normal"
                else:
                    self._telnet_state = "normal"
            elif self._telnet_state == "negotiation":
                # option byte — just consume it
                self._telnet_state = "normal"
            elif self._telnet_state == "subneg":
                if b == IAC:
                    self._telnet_state = "subneg_iac"
            elif self._telnet_state == "subneg_iac":
                if b == SE:
                    self._telnet_state = "normal"
                else:
                    self._telnet_state = "subneg"

            i += 1
        return "".join(text)

    # ── Background reader ──────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """Background thread that reads from the telnet socket.

        Accumulates text into self._response_text and sets
        self._prompt_event when a prompt line is detected.
        """
        self._sock.settimeout(0.5)
        while self._reader_running:
            try:
                data = self._sock.recv(4096)
                if not data:
                    logger.info("Socket closed by Renode")
                    self._reader_running = False
                    self._prompt_event.set()  # unblock any waiting execute()
                    break
            except socket.timeout:
                continue
            except OSError:
                self._reader_running = False
                self._prompt_event.set()
                break

            text = self._strip_iac(data)
            if not text:
                continue

            self._response_text += text

            # Check if we've received a prompt
            # Look at the last line of accumulated text
            lines = self._response_text.split("\n")
            last_line = lines[-1] if lines else ""
            if PROMPT_RE.search(last_line):
                self._prompt_event.set()
