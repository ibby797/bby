#!/usr/bin/env python3
"""Build quantbot.pyz — the whole bot in one runnable file.

Uses the standard-library ``zipapp`` module: the quantbot package plus an
entry point are zipped into a single file that runs anywhere Python 3.10+ and
the dependencies are available:

    python make_bundle.py
    python quantbot.pyz --help
    python quantbot.pyz signals --out signals.json

Dependencies (pandas/numpy/etc. contain compiled code, so they can't live
inside the zip portably) are installed once per machine:

    pip install -r requirements.txt
"""

from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = ROOT / "quantbot.pyz"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "app"
        shutil.copytree(
            ROOT / "quantbot",
            staging / "quantbot",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (staging / "__main__.py").write_text(
            "from quantbot.cli import main\n"
            "raise SystemExit(main())\n"
        )
        zipapp.create_archive(
            staging,
            target=TARGET,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    size_kb = TARGET.stat().st_size / 1024
    print(f"built {TARGET.name} ({size_kb:.0f} KiB)")
    print("run it anywhere with:  python quantbot.pyz --help")


if __name__ == "__main__":
    main()
