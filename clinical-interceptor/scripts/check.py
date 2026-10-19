"""Runs every local quality gate in order and stops at the first failure.

    python scripts/check.py            # ruff check, mypy, tests
    python scripts/check.py --format   # also ruff format --check (the code is not yet formatted with it)

Tests that need PostgreSQL run only when INTERCEPTOR_TEST_DSN is set; otherwise they are skipped and the run says so.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def steps(with_format):
    py = [sys.executable, "-m"]
    yield "ruff check", py + ["ruff", "check", "."]
    if with_format:
        yield "ruff format", py + ["ruff", "format", "--check", "."]
    yield "mypy", py + ["mypy"]
    yield "tests", py + ["unittest", "discover", "-s", "tests", "-t", "."]


def main():
    with_format = "--format" in sys.argv
    if not os.environ.get("INTERCEPTOR_TEST_DSN"):
        print("note: INTERCEPTOR_TEST_DSN is not set, database tests will be skipped")
    for name, command in steps(with_format):
        print(f"== {name}")
        if subprocess.run(command, cwd=ROOT).returncode != 0:  # noqa: S603
            print(f"FAILED: {name}")
            return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
