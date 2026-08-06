"""Job state and brief composition for Icon Studio.

State lives in the app's workspace directory, NOT in the app install dir: the
install dir is re-copied from the repo on every update, so anything durable
there would be clobbered (and anything committed there would ship the author's
data to every user).

Standalone by design — no ``kiro_crew`` imports — so the agent-facing CLI can
reuse it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contact_sheet import (
    data_home,
    ledger_path,
    proof_path,
    render_dir,
    state_path,
    workspace_dir,
)

VALID_MODES = ("ship", "concepts")
VALID_STYLES = ("outline", "filled")
VALID_KEYLINES = ("square", "circle")
#: ``new`` diverges on metaphors; ``redraw`` re-renders the library's existing
#: icons at changed parameters and must NOT re-diverge -- see :func:`compose_brief`.
VALID_JOB_KINDS = ("new", "redraw")
MAX_NAMES = 24
MAX_LIBRARIES = 40

#: The parameters a library owns. Every job in a library inherits these verbatim,
#: which is the whole point of a library: two requests a week apart must produce
#: icons that sit in the same set. A job may NOT override them.
LIBRARY_PARAM_KEYS = ("canvas", "sizes", "stroke", "style", "keyline")

DEFAULT_LIBRARY_NAME = "Default library"

_LEDGER_SEED = """# Metaphor ledger

One row per shipped icon. The icon-designer agent reads this before designing so
it never reuses a spent metaphor or ships two icons with the same silhouette.

| Icon | Metaphor used | Rejected | Notes |
| --- | --- | --- | --- |
"""


def ensure_workspace() -> Path:
    """Create the workspace layout and seed files. Idempotent."""
    root = workspace_dir()
    for sub in ("", "icons", "proofs", "ledgers"):
        (root / sub if sub else root).mkdir(parents=True, exist_ok=True)
    if not state_path().is_file():
        _atomic_write_text(
            state_path(), json.dumps({"libraries": [], "jobs": []}, indent=2) + "\n"
        )
    if not ledger_path().is_file():
        _atomic_write_text(ledger_path(), _LEDGER_SEED)
    return root


def load_state() -> dict[str, Any]:
    ensure_workspace()
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"libraries": [], "jobs": []}
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return {"libraries": [], "jobs": []}
    if not isinstance(data.get("libraries"), list):
        data["libraries"] = []
    if _migrate(data):
        save_state(data)
    return data


def save_state(state: dict[str, Any]) -> None:
    ensure_workspace()
    _atomic_write_text(state_path(), json.dumps(state, indent=2) + "\n")


def _migrate(state: dict[str, Any]) -> bool:
    """Adopt orphan jobs into a library. Returns True if state changed.

    Jobs predate libraries, so a state file written by the previous version has
    ``jobs`` with no ``libraryId`` and no ``libraries`` at all. Rather than drop
    them, seed a library from the oldest job's own parameters -- that set is by
    definition consistent with at least one job, which a hardcoded default
    would not be.
    """
    jobs = state.get("jobs", [])
    libs = state.get("libraries", [])
    orphans = [j for j in jobs if not j.get("libraryId")]
    if not orphans and (libs or not jobs):
        return False

    if libs:
        target = libs[0]
    else:
        seed = orphans[-1].get("params", {}) if orphans else {}
        target = _library_record(DEFAULT_LIBRARY_NAME, _library_params_from(seed))
        state["libraries"] = [target]
        _seed_library_ledger(target["id"], target["name"])

    for job in orphans:
        job["libraryId"] = target["id"]
    return True


# --- output folders -------------------------------------------------------------
#
# Every library owns a folder on disk holding its SVGs, flat, one file per icon
# name. Flat rather than per-job is a requirement, not a preference: a redraw
# overwrites an icon in place, which needs exactly one canonical location per
# name. The default sits under the Kiro Crew workspace so a new library needs no
# decision from the user; they can repoint it at any local folder afterwards.


def libraries_root() -> Path:
    """Default parent for library output folders."""
    return workspace_dir() / "libraries"


def library_output_dir(library: dict[str, Any]) -> Path:
    """The folder a library's icons live in, falling back to the default.

    Libraries created before output paths existed have no ``outputPath``; deriving
    the default from the name keeps them readable instead of blank.
    """
    raw = str((library or {}).get("outputPath") or "")
    if raw:
        return Path(raw).expanduser()
    return libraries_root() / _slug(str((library or {}).get("name") or "library"))


def default_output_path(name: str, taken: Any = ()) -> str:
    """A collision-free default folder for a library called *name*."""
    base = _slug(name)
    used = {str(t) for t in taken}
    candidate = libraries_root() / base
    n = 2
    while str(candidate) in used:
        candidate = libraries_root() / f"{base}-{n}"
        n += 1
    return str(candidate)


def normalize_output_path(raw: Any, fallback: str) -> str:
    """Validate a user-supplied output folder. Raises ValueError when unsafe.

    This path is written verbatim into the agent's brief as a write target, so it
    is a privilege boundary, not a preference. The agent runs in-process with the
    gateway's privileges; a path pointing at ``~/.ssh`` or at the app's own
    install directory would turn "change my output folder" into arbitrary
    overwrite of credentials or of the app's Python.

    Validation is deliberately absolute-only and reject-only -- no silent
    rewriting. A path that is nearly right is a path the user should fix.
    """
    text = str(raw or "").strip()
    if not text:
        return fallback
    if "\x00" in text or len(text) > 1024:
        raise ValueError("output folder path is not valid")

    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("output folder must be an absolute path")
    resolved = path.resolve()

    if resolved == Path(resolved.anchor):
        raise ValueError("output folder cannot be the filesystem root")
    if any(part == ".git" for part in resolved.parts):
        raise ValueError("output folder cannot be inside a .git directory")

    for blocked in _blocked_output_roots():
        if resolved == blocked or _is_within(resolved, blocked):
            raise ValueError(f"output folder is not allowed: {blocked} is protected")

    # The Kiro Crew data home holds agents, credentials, and app code. Only the
    # app's own workspace subtree is a legitimate target inside it.
    home = data_home().resolve()
    if (resolved == home or _is_within(resolved, home)) and not _is_within(
        resolved, workspace_dir().resolve()
    ):
        raise ValueError(
            "output folder must be outside the Kiro Crew data home, or inside "
            f"{workspace_dir()}"
        )

    if resolved.exists() and not resolved.is_dir():
        raise ValueError("output folder path already exists and is not a directory")

    # Defence in depth: the host's own sensitive-path denylist knows about files
    # this app has never heard of. Best-effort -- store.py must stay importable
    # without the gateway installed, which is how its tests run.
    try:
        from kiro_crew.security import is_sensitive_path  # type: ignore

        if is_sensitive_path(str(resolved)):
            raise ValueError("output folder is a protected location")
    except ImportError:
        pass

    return str(resolved)


def ensure_output_dir(library: dict[str, Any]) -> Path:
    """Create a library's output folder if it is missing. Returns it."""
    path = library_output_dir(library)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _blocked_output_roots() -> tuple[Path, ...]:
    """System and credential locations that are never valid output folders."""
    home = Path.home()
    names = (
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        ".docker",
        ".config",
        ".local/share/keyrings",
        "Library/Keychains",
    )
    roots = [home / n for n in names]
    # Targeted rather than all of /var: on macOS the system temp dir resolves to
    # /private/var/folders, and refusing a scratch directory buys no safety while
    # breaking a legitimate destination.
    roots += [
        Path(p)
        for p in (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/System",
            "/private/etc",
            "/var/db",
            "/var/log",
            "/var/root",
        )
    ]
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return tuple(out)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _slug(text: str) -> str:
    """Folder-safe slug. Always non-empty, never a path traversal."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:48]
    return out or "library"


def normalize_library_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate the five parameters a library owns. Raises ValueError on garbage."""
    canvas = _int(raw.get("canvas"), 16, 8, 512)
    sizes = raw.get("sizes")
    if isinstance(sizes, list) and sizes:
        size_list = sorted({_int(s, canvas, 8, 512) for s in sizes})
    else:
        size_list = sorted({canvas, 24, 32})

    style = str(raw.get("style") or "outline")
    keyline = str(raw.get("keyline") or "square")
    if style not in VALID_STYLES:
        raise ValueError(f"style must be one of {VALID_STYLES}")
    if keyline not in VALID_KEYLINES:
        raise ValueError(f"keyline must be one of {VALID_KEYLINES}")

    return {
        "canvas": canvas,
        "sizes": size_list,
        "stroke": _float(raw.get("stroke"), 1.0, 0.25, 8.0),
        "style": style,
        "keyline": keyline,
    }


def normalize_job_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate the per-request fields: what to draw, and how involved the user is."""
    names_raw = raw.get("names")
    if isinstance(names_raw, str):
        names_raw = [n for n in names_raw.replace(",", "\n").splitlines()]
    if not isinstance(names_raw, list):
        raise ValueError("names must be a list or newline-separated string")
    names = [str(n).strip() for n in names_raw if str(n).strip()]
    if not names:
        raise ValueError("at least one icon name is required")

    kind = str(raw.get("kind") or "new")
    if kind not in VALID_JOB_KINDS:
        raise ValueError(f"kind must be one of {VALID_JOB_KINDS}")

    # A redraw covers whatever the library already holds, so MAX_NAMES -- a limit
    # on how much divergent thinking to ask for in one go -- does not apply. The
    # ledger cap still bounds it in practice.
    if kind == "new" and len(names) > MAX_NAMES:
        raise ValueError(f"at most {MAX_NAMES} icons per request")

    mode = str(raw.get("mode") or "concepts")
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")

    return {
        "names": names,
        "mode": "ship" if kind == "redraw" else mode,
        "kind": kind,
        "notes": str(raw.get("notes") or "").strip()[:2000],
    }


def normalize_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Legacy single-payload validator: library params + job fields in one dict.

    Kept so the flat shape stored on ``job['params']`` has exactly one definition.
    New callers should use the two functions above and merge via
    :func:`merge_job_params`.
    """
    return {**normalize_job_fields(raw), **normalize_library_params(raw)}


def merge_job_params(library: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """The job's stored ``params``: library parameters win, always.

    A job cannot override canvas/stroke/style/keyline/sizes -- that is what makes
    a library a library. The merged shape is identical to the pre-library one, so
    :func:`compose_brief`, the renderer, and the UI need no special-casing.
    """
    return {**fields, **_library_params_from(library.get("params", {}))}


def new_library(name: str, params: dict[str, Any], output_path: Any = None) -> dict[str, Any]:
    """Create a library, seed its metaphor ledger, and create its output folder."""
    state = load_state()
    libs = state.get("libraries", [])
    if len(libs) >= MAX_LIBRARIES:
        raise ValueError(f"at most {MAX_LIBRARIES} libraries")
    clean = str(name or "").strip()[:80]
    if not clean:
        raise ValueError("library name is required")
    taken = [str(lib.get("outputPath") or "") for lib in libs]
    resolved = normalize_output_path(output_path, default_output_path(clean, taken))
    lib = _library_record(clean, params, resolved)
    state["libraries"] = [*libs, lib]
    save_state(state)
    _seed_library_ledger(lib["id"], lib["name"])
    ensure_output_dir(lib)
    return lib


def update_library(lib_id: str, **fields: Any) -> dict[str, Any] | None:
    """Rename a library, change its parameters, and/or repoint its output folder.

    Changing parameters does NOT retro-edit finished jobs: their stored params
    record what was actually drawn. New requests pick up the new set.

    Changing the output folder copies existing SVGs across rather than moving
    them. Copying is the conservative choice: the gallery reads from the current
    folder, so a move that half-failed would make icons vanish, and nothing here
    is worth deleting a user's only copy of a file for.
    """
    state = load_state()
    for lib in state.get("libraries", []):
        if lib.get("id") != lib_id:
            continue
        if "name" in fields:
            clean = str(fields["name"] or "").strip()[:80]
            if not clean:
                raise ValueError("library name is required")
            lib["name"] = clean
        if "params" in fields:
            lib["params"] = normalize_library_params(fields["params"] or {})
        if "outputPath" in fields:
            previous = library_output_dir(lib)
            resolved = normalize_output_path(
                fields["outputPath"], default_output_path(lib.get("name") or "library")
            )
            lib["outputPath"] = resolved
            target = ensure_output_dir(lib)
            if target != previous:
                _copy_svgs(previous, target)
        save_state(state)
        return lib
    return None


def _copy_svgs(source: Path, target: Path) -> int:
    """Copy ``*.svg`` from *source* into *target*, never overwriting. Best-effort."""
    if not source.is_dir():
        return 0
    copied = 0
    for path in sorted(source.glob("*.svg")):
        dest = target / path.name
        if dest.exists():
            continue
        try:
            shutil.copy2(path, dest)
            copied += 1
        except OSError:
            continue
    return copied


def get_library(lib_id: str) -> dict[str, Any] | None:
    for lib in load_state().get("libraries", []):
        if lib.get("id") == lib_id:
            return lib
    return None


def public_library(state: dict[str, Any], lib: dict[str, Any]) -> dict[str, Any]:
    """Library plus the counts and resolved paths the UI needs."""
    lid = lib.get("id")
    jobs = [j for j in state.get("jobs", []) if j.get("libraryId") == lid]
    out = dict(lib)
    out["jobCount"] = len(jobs)
    out["iconCount"] = sum(len(j.get("icons") or []) for j in jobs)
    # Resolved rather than stored: pre-output-path libraries have no stored value,
    # and the UI should show where files actually land, not an empty string.
    out["outputPath"] = str(library_output_dir(lib))
    out["defaultOutputPath"] = str(libraries_root() / _slug(str(lib.get("name") or "library")))
    return out


def new_job(library: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Append a queued job to a library and create its icon directory."""
    state = load_state()
    job_id = _next_job_id(state)
    job = {
        "id": job_id,
        "libraryId": library["id"],
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "queued",
        "params": merge_job_params(library, fields),
        "icons": [],
        "proof": "",
        "note": "",
    }
    # The library's output folder, not a per-job one: the icons are the library's.
    ensure_output_dir(library)
    state["jobs"] = [job, *state.get("jobs", [])]
    save_state(state)
    return job


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    state = load_state()
    for job in state.get("jobs", []):
        if job.get("id") == job_id:
            job.update(fields)
            save_state(state)
            return job
    return None


def get_job(job_id: str) -> dict[str, Any] | None:
    for job in load_state().get("jobs", []):
        if job.get("id") == job_id:
            return job
    return None


def compose_brief(job: dict[str, Any], library: dict[str, Any] | None = None) -> str:
    """The message handed to the icon-designer agent.

    Carries every path explicitly. The agent config declares no relative
    resources, so nothing here depends on the session's working directory.
    """
    p = job["params"]
    jid = job["id"]
    lib_id = job.get("libraryId") or ""
    lib_name = (library or {}).get("name") or lib_id or "(none)"
    out_dir = library_output_dir(library or {})
    ledger = library_ledger_path(lib_id) if lib_id else ledger_path()
    names = "\n".join(f"  - {n}" for n in p["names"])
    is_redraw = p.get("kind") == "redraw"
    if is_redraw:
        directive = (
            "REDRAW — the parameters below changed and this library's existing "
            "icons must be re-rendered to match them.\n\n"
            "Do NOT diverge on metaphors and do NOT invent new ones. Every icon "
            "listed already has a metaphor recorded in the ledger; read it and "
            "redraw that same metaphor at the new parameters. Changing a "
            "silhouette here is a defect: the user asked for a different stroke "
            "or canvas, not a different idea.\n\n"
            "If a listed icon has no ledger entry, say so and skip it rather "
            "than guessing what it used to be. Overwrite the existing SVG files."
        )
    elif p["mode"] == "ship":
        directive = (
            "SHIP — do not wait for approval. Choose the strongest metaphor yourself, "
            "state in one line which you chose and what you rejected, then draw."
        )
    else:
        directive = (
            "CONCEPTS — stop after step 2. Present three metaphors per icon and wait "
            "for the user to pick. Do not draw yet."
        )
    notes = f"\n\nDesigner's notes:\n{p['notes']}" if p["notes"] else ""

    heading = "redraw" if is_redraw else "job"
    listing = "Icons to redraw" if is_redraw else "Icons requested"
    ledger_note = (
        """The ledger above is scoped to THIS library and records the metaphor behind
every icon in it. It is your source of truth for this redraw: look each icon up
and reproduce its metaphor. Update a row only if the new parameters forced a
genuine construction change, and say which rows you touched."""
        if is_redraw
        else """The ledger above is scoped to THIS library and lists every metaphor already
spent in it. Read it before you diverge and append to it before you finish: an
icon that repeats a silhouette already in the library is a defect, even if the
icon is good on its own."""
    )
    return f"""Icon Studio {heading} {jid}, in library "{lib_name}".

{directive}

{listing} ({len(p['names'])}):
{names}

Parameters (owned by the library — do not deviate, these are what make the
library's icons a set rather than a pile):
  - canvas: {p['canvas']}x{p['canvas']}
  - proof sizes: {', '.join(str(s) for s in p['sizes'])}px
  - stroke: {p['stroke']}px
  - style: {p['style']}
  - keyline: {p['keyline']}

Paths (use these exactly):
  - SVG output dir: {out_dir}
  - metaphor ledger: {ledger}
  - state file:      {state_path()}
  - contact sheet:   python3 {app_scripts_dir() / 'contact_sheet.py'} --job {jid}

Write one file per icon, named after the icon, directly in the output dir above:
{out_dir}/<icon-name>.svg — no per-request subfolder. That folder is the whole
library's icon set, which is why a redraw overwrites a file in place instead of
creating a second copy of the same icon under a new request.

{ledger_note}

Keep job {jid} in the state file current as you go: set `status` to concepts,
drawing, proofing, then done (or failed with a `note`), and fill `icons` with
one entry per shipped file: {{"name", "file", "metaphor"}} where `file` is the
bare file name inside the output dir.{notes}
"""


def library_ledger_path(lib_id: str) -> Path:
    """Per-library ledger. Consistency is a within-library property, so the
    ledger has to be too -- one global ledger would make two unrelated icon sets
    compete for the same metaphors."""
    return workspace_dir() / "ledgers" / f"{_safe_id(lib_id)}.md"


def library_icon_names(lib_id: str) -> list[str]:
    """Distinct icon names shipped in a library, newest occurrence first.

    This is the roster a redraw covers. Deliberately drawn from ``icons``
    (what was actually shipped) rather than from requested ``params['names']``:
    a name that was asked for but never drawn has no ledger entry, so a redraw
    has nothing to preserve for it.
    """
    seen: dict[str, None] = {}
    for job in load_state().get("jobs", []):
        if job.get("libraryId") != lib_id:
            continue
        for icon in job.get("icons") or []:
            name = str(icon.get("name") or "").strip()
            if name and name not in seen:
                seen[name] = None
    return list(seen)


def library_icons(lib_id: str) -> list[dict[str, Any]]:
    """Every icon shipped in a library, newest job first, with sanitized markup.

    The markup is inlined by the UI (an <img> cannot resolve ``currentColor``,
    which is the house default), so it is sanitized here -- see
    :func:`sanitize_svg` for why that is not optional.
    """
    state = load_state()
    library = next((l for l in state.get("libraries", []) if l.get("id") == lib_id), None)
    out_dir = library_output_dir(library or {})
    out: list[dict[str, Any]] = []
    for job in state.get("jobs", []):
        if job.get("libraryId") != lib_id:
            continue
        for icon in job.get("icons") or []:
            rel = str(icon.get("file") or "")
            out.append(
                {
                    "name": str(icon.get("name") or ""),
                    "metaphor": str(icon.get("metaphor") or ""),
                    "file": rel,
                    "jobId": job.get("id", ""),
                    "svg": _read_icon_svg(rel, out_dir),
                }
            )
    return out


def _read_icon_svg(rel: str, out_dir: Path) -> str:
    """Read and sanitize one icon, from the library folder or the legacy location.

    Two roots because two eras: the agent now writes flat into the library's
    output folder and records a bare filename, but jobs from before output paths
    existed recorded a path relative to the workspace. Each root is containment-
    checked separately -- a filename is only ever resolved inside a root we chose,
    never wherever a state-file string points.
    """
    if not rel:
        return ""
    for root, candidate in (
        (out_dir, out_dir / Path(rel).name),
        (workspace_dir(), workspace_dir() / rel),
    ):
        try:
            path = candidate.resolve()
            path.relative_to(root.resolve())
            if not path.is_file():
                continue
            return sanitize_svg(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return ""


def render_job_sheet(job_id: str, sizes: list[int] | None = None):
    """Render a job's contact sheet from its library's output folder.

    Job-scoped, not library-scoped: the sheet proves the icons THIS request
    produced. Since the folder is flat and shared by every request in the
    library, the job's own icon names are the filter.
    """
    job = get_job(job_id)
    if job is None:
        raise ValueError(f"no such job: {job_id}")
    library = get_library(str(job.get("libraryId") or "")) or {}
    names = [str(i.get("name") or "") for i in (job.get("icons") or [])]
    names = [n for n in names if n]
    return render_dir(
        library_output_dir(library),
        proof_path(job_id, 1),
        proof_path(job_id, 2),
        sizes=sizes or (job.get("params", {}).get("sizes") or None),
        title=f"Icon Studio — {job_id}",
        names=names or None,
    )


_SVG_BAD_TAGS = ("script", "foreignobject", "iframe", "style", "image", "use", "animate")


def sanitize_svg(markup: str) -> str:
    """Strip anything executable or externally-referencing from agent-authored SVG.

    This is a real control, not defensive noise: KiroCrew app UIs mount directly
    into the dashboard DOM rather than an iframe, so inlining an SVG verbatim
    would run any ``<script>`` or ``onload=`` it contains with the dashboard's
    own origin and session. The agent is trusted-ish, but its output is a file on
    disk that anything else can also write to.

    Deliberately a whitelist-shaped strip rather than a parser: no <style>, no
    external refs, no event handlers, and a size ceiling.
    """
    text = str(markup or "")
    if len(text) > 64_000:
        return ""
    low = text.lower()
    if "<svg" not in low:
        return ""
    for tag in _SVG_BAD_TAGS:
        if f"<{tag}" in low:
            return ""
    # Event handlers (on*=) and javascript: / data: URLs in any attribute.
    if re.search(r"\son[a-z]+\s*=", low) or "javascript:" in low:
        return ""
    if re.search(r"(?:xlink:)?href\s*=\s*[\"'](?!#)", low):
        return ""
    start = low.index("<svg")
    end = low.rindex("</svg>") + len("</svg>")
    return text[start:end]


def app_scripts_dir() -> Path:
    """Scripts dir of the INSTALLED app, which is what the agent can run."""
    override = os.environ.get("ICON_STUDIO_APP_DIR")
    if override:
        return Path(override).expanduser() / "scripts"
    from .contact_sheet import app_dir

    return app_dir() / "scripts"


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Job plus derived fields the UI needs."""
    jid = job.get("id", "")
    out = dict(job)
    out["hasProof"] = bool(jid) and proof_path(jid, 1).is_file()
    return out


# --- internals -----------------------------------------------------------------


def _library_record(name: str, params: dict[str, Any], output_path: str = "") -> dict[str, Any]:
    return {
        "id": _new_library_id(),
        "name": name,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": normalize_library_params(params or {}),
        "outputPath": output_path or default_output_path(name),
    }


def _library_params_from(source: dict[str, Any]) -> dict[str, Any]:
    """Pull only the library-owned keys out of any params-shaped dict."""
    return normalize_library_params({k: source.get(k) for k in LIBRARY_PARAM_KEYS})


def _new_library_id() -> str:
    return "lib-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:20]


def _safe_id(value: str) -> str:
    """Filesystem-safe library id -- validates, never sanitizes.

    Stripping bad characters is the wrong shape for a path guard: it turns
    ``../../etc/passwd`` into the legal-but-nonsense ``....etcpasswd`` and
    silently collapses distinct ids onto one file. Ids are minted by
    :func:`_new_library_id`, so anything not matching that shape is a bug or an
    attack -- refuse it either way.
    """
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", text) or ".." in text:
        raise ValueError("invalid library id")
    return text


def _seed_library_ledger(lib_id: str, name: str) -> None:
    path = library_ledger_path(lib_id)
    if path.is_file():
        return
    _atomic_write_text(path, _LEDGER_SEED.replace("# Metaphor ledger", f"# Metaphor ledger — {name}"))


def _next_job_id(state: dict[str, Any]) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    used = {str(j.get("id", "")) for j in state.get("jobs", [])}
    for n in range(1, 1000):
        candidate = f"{day}-{n:03d}"
        if candidate not in used:
            return candidate
    raise ValueError("too many jobs today")


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, out))


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, round(out, 3)))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
