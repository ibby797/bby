#!/usr/bin/env python3
"""Thin launcher for the quantbot CLI (see quantbot/cli.py).

Equivalent to running ``quantbot`` after ``pip install .`` or
``python quantbot.pyz`` with the single-file build.
"""

from quantbot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
