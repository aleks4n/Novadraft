"""Project-wide Python env bootstrap.

The novadraft project requires the `novadraft` conda env (Python 3.12, with
marker-pdf + litellm + fastapi). The interpreter is declared in
`.python-env` at the project root.

Import this module at the top of any entry-point script. If the current
interpreter doesn't match the declared one, the process re-execs itself with
the correct Python — transparent to the caller.

    import _env_bootstrap  # noqa: F401
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_DECL_FILE = _PROJECT_ROOT / ".python-env"
_GUARD_ENV = "NOVADRAFT_ENV_BOOTSTRAPPED"


def _declared_python() -> Path | None:
    if not _DECL_FILE.exists():
        return None
    line = _DECL_FILE.read_text().strip().splitlines()[0].strip()
    if not line or line.startswith("#"):
        return None
    return Path(line)


def _ensure() -> None:
    if os.environ.get(_GUARD_ENV):
        return
    declared = _declared_python()
    if declared is None or not declared.exists():
        return
    current = Path(sys.executable).resolve()
    if current == declared.resolve():
        return
    os.environ[_GUARD_ENV] = "1"
    os.execv(str(declared), [str(declared), *sys.argv])


_ensure()
