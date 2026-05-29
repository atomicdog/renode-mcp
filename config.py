import os
from pathlib import Path

# Renode installation.
# Point RENODE_PATH at a built Renode source tree (the directory that contains
# output/bin/Release/Renode.dll). Required for renode_start to work.
RENODE_DIR = os.getenv("RENODE_PATH", "")
RENODE_BIN = os.path.join(RENODE_DIR, "output/bin/Release/Renode.dll") if RENODE_DIR else ""
RENODE_TELNET_PORT = int(os.getenv("RENODE_TELNET_PORT", "1234"))

# Optional directory of .resc scripts to expose by name.
# If set, every *.resc file in this directory (recursively) becomes available to
# renode_start / renode_load_script under its filename stem (e.g. "my_board" for
# "my_board.resc"). Scripts may also always be referenced by absolute path.
SCRIPTS_DIR = os.getenv("RENODE_SCRIPTS_DIR", "")

# name -> path map, populated by discover_scripts().
AVAILABLE_SCRIPTS: dict[str, str] = {}


def discover_scripts() -> None:
    """Scan SCRIPTS_DIR for .resc scripts and index them by filename stem."""
    if not SCRIPTS_DIR:
        return
    scripts_dir = Path(SCRIPTS_DIR)
    if not scripts_dir.is_dir():
        return
    for resc in sorted(scripts_dir.rglob("*.resc")):
        # Later matches with the same stem win; keep it simple and predictable.
        AVAILABLE_SCRIPTS.setdefault(resc.stem, str(resc))


# Auto-discover on import.
discover_scripts()
