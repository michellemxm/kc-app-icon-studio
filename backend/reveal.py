"""Open a library's output folder in the OS file manager.

Separate from ``store`` on purpose: this is the one place in the app that hands a
path to another program, and the rules that make that safe are worth keeping in
one small file rather than scattered through state handling.

The hardening here is lifted from Kiro Crew's own Notes builtin
(``kiro_crew/apps/builtins/md_notebook/server.py``), which already worked through
the traps. Two of them are not obvious:

* **The binary is an absolute path, never resolved through PATH.** The front of
  the inherited PATH can be agent-writable (``~/.local/bin`` is the usual one), so
  a planted ``open`` / ``xdg-open`` would be what this launches -- on a click the
  user has every reason to trust.
* **``xdg-open`` is a shell script** that dispatches to whichever helper it finds
  on PATH (``gio``, ``gvfs-open``, ``exo-open``, ``kde-open``, ``dbus-send``), so
  pinning our own PATH is not enough on its own -- the child lookup has to be
  pinned too. Hence ``trusted_env()``.

Everything else follows from those: fixed argv, no shell, a timeout, and a path
the CALLER derives from the library record. Nothing a request body carries ever
reaches this module.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Long enough for a cold Finder/file-manager launch, short enough that a wedged
#: helper does not pin a gateway thread indefinitely.
REVEAL_TIMEOUT_SEC = 20

#: Same pin, and the same reason, as the host's ``git_ops.TRUSTED_PATH``.
TRUSTED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

REVEAL_BINARIES = {
    "darwin": "/usr/bin/open",
    "linux": "/usr/bin/xdg-open",
}


class RevealError(Exception):
    """Could not open the folder. Carries the HTTP status the route should send."""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


def reveal_binary() -> str | None:
    """The absolute file-manager binary for this platform, or None.

    Existence-checked rather than assumed: a minimal Linux container has no
    ``xdg-open``, and the app's manifest does not claim Windows. A missing binary
    must read as "not supported here" rather than as a crash.
    """
    if sys.platform.startswith("win"):
        return None
    key = "darwin" if sys.platform == "darwin" else "linux"
    binary = REVEAL_BINARIES.get(key)
    return binary if binary and os.path.isfile(binary) else None


def trusted_env() -> dict[str, str]:
    """The current environment with PATH pinned to trusted system directories.

    Only PATH is replaced. The rest is what lets the file manager reach the
    running desktop session (``DISPLAY``, ``DBUS_SESSION_BUS_ADDRESS``, ``XDG_*``).
    """
    env = dict(os.environ)
    if os.name == "posix":
        env["PATH"] = TRUSTED_PATH
    return env


def reveal_folder(path: Path) -> None:
    """Open *path* in the OS file manager. Blocking -- callers must offload it.

    *path* is computed by the caller from the library record, never taken from a
    request. The argv is a fixed absolute binary plus that one path, with no
    shell, so there is nothing here for a crafted string to escape into.
    """
    binary = reveal_binary()
    if binary is None:
        raise RevealError("opening a folder is not supported on this system", 501)
    try:
        proc = subprocess.run(  # noqa: S603 - fixed absolute argv, no shell
            [binary, str(path)],
            capture_output=True,
            text=True,
            timeout=REVEAL_TIMEOUT_SEC,
            check=False,
            env=trusted_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RevealError("the file manager did not respond", 504) from exc
    except OSError as exc:
        raise RevealError(f"could not open the folder: {exc}", 500) from exc
    if proc.returncode != 0:
        raise RevealError("could not open the folder", 500)
