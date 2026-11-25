"""Run the installed CLI with process-scoped macOS network restrictions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

from feasibility.direct_smoke import network_probe

PROFILE = Path(__file__).resolve().parent / "feasibility/offline.sb"


def main():
    if not sys.argv[1:]:
        raise SystemExit("usage: python tools/verify_offline.py <vision-tables command and options>")
    if sys.argv[1] != "--restricted":
        sandbox = shutil.which("sandbox-exec")
        if not sandbox:
            raise SystemExit("offline verification blocked: macOS sandbox-exec is unavailable")
        os.execv(sandbox, [sandbox, "-f", str(PROFILE), sys.executable, __file__,
                           "--restricted", *sys.argv[1:]])
    probe = network_probe()
    os.environ["VT_VERIFY_OUTER_SANDBOX"] = "1"
    print(json.dumps({"offline_probe": probe}), file=sys.stderr, flush=True)
    # Both the import and child model processes execute under the tested profile.
    sys.argv = ["vision-tables", *sys.argv[2:]]
    from vision_tables.cli import main as cli
    return cli()


if __name__ == "__main__":
    sys.exit(main())
