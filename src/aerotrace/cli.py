"""Command-line entry point. Subcommands are added by later features."""

from __future__ import annotations

import argparse

from aerotrace import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aerotrace", description="Change impact for C and C++ code.")
    parser.add_argument("--version", action="version", version=f"aerotrace {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
