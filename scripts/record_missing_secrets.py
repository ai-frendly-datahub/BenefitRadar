"""Record missing optional secrets without printing their values."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from benefitradar.collection_contract import emit_missing_secret_warning, log_skip

_ENV_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main(argv: list[str] | None = None) -> int:
    names = list(sys.argv[1:] if argv is None else argv)
    if not names:
        print("usage: record_missing_secrets.py ENV_VAR_NAME [ENV_VAR_NAME...]", file=sys.stderr)
        return 2

    raw_dir = Path("data/raw")
    for name in names:
        if _ENV_VAR_NAME.fullmatch(name) is None:
            print("required_env must be an environment variable name", file=sys.stderr)
            return 2
        if os.environ.get(name, "").strip():
            continue
        emit_missing_secret_warning("failure-notify", name)
        log_skip(raw_dir=raw_dir, source_name="failure-notify", required_env=name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
