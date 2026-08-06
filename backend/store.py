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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contact_sheet import job_dir, ledger_path, proof_path, state_path, workspace_dir

VALID_MODES = ("ship", "concepts")
VALID_STYLES = ("outline", "filled")
VALID_KEYLINES = ("square", "circle")
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
    if len(names) > MAX_NAMES:
        raise ValueError(f"at most {MAX_NAMES} icons per request")

    mode = str(raw.get("mode") or "concepts")
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")

    return {
        "names": names,
        "mode": mode,
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


def new_library(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Create a library and seed its own metaphor ledger."""
    state = load_state()
    if len(state.get("libraries", [])) >= MAX_LIBRARIES:
        raise ValueError(f"at most {MAX_LIBRARIES} libraries")
    clean = str(name or "").strip()[:80]
    if not clean:
        raise ValueError("library name is required")
    lib = _library_record(clean, params)
    state["libraries"] = [*state.get("libraries", []), lib]
    save_state(state)
    _seed_library_ledger(lib["id"], lib["name"])
    return lib


def update_library(lib_id: str, **fields: Any) -> dict[str, Any] | None:
    """Rename a library and/or change its parameters.

    Changing parameters does NOT retro-edit finished jobs: their stored params
    record what was actually drawn. New requests pick up the new set.
    """
    state = load_state()
    for lib in state.get("libraries", []):
        if lib.get("id") == lib_id:
            if "name" in fields:
                clean = str(fields["name"] or "").strip()[:80]
                if not clean:
                    raise ValueError("library name is required")
                lib["name"] = clean
            if "params" in fields:
                lib["params"] = normalize_library_params(fields["params"] or {})
            save_state(state)
            return lib
    return None


def get_library(lib_id: str) -> dict[str, Any] | None:
    for lib in load_state().get("libraries", []):
        if lib.get("id") == lib_id:
            return lib
    return None


def public_library(state: dict[str, Any], lib: dict[str, Any]) -> dict[str, Any]:
    """Library plus the counts the left panel header needs."""
    lid = lib.get("id")
    jobs = [j for j in state.get("jobs", []) if j.get("libraryId") == lid]
    out = dict(lib)
    out["jobCount"] = len(jobs)
    out["iconCount"] = sum(len(j.get("icons") or []) for j in jobs)
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
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
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
    ledger = library_ledger_path(lib_id) if lib_id else ledger_path()
    names = "\n".join(f"  - {n}" for n in p["names"])
    directive = (
        "SHIP — do not wait for approval. Choose the strongest metaphor yourself, "
        "state in one line which you chose and what you rejected, then draw."
        if p["mode"] == "ship"
        else "CONCEPTS — stop after step 2. Present three metaphors per icon and wait "
        "for the user to pick. Do not draw yet."
    )
    notes = f"\n\nDesigner's notes:\n{p['notes']}" if p["notes"] else ""

    return f"""Icon Studio job {jid}, in library "{lib_name}".

{directive}

Icons requested ({len(p['names'])}):
{names}

Parameters (owned by the library — do not deviate, these are what make the
library's icons a set rather than a pile):
  - canvas: {p['canvas']}x{p['canvas']}
  - proof sizes: {', '.join(str(s) for s in p['sizes'])}px
  - stroke: {p['stroke']}px
  - style: {p['style']}
  - keyline: {p['keyline']}

Paths (use these exactly):
  - SVG output dir: {job_dir(jid)}
  - metaphor ledger: {ledger}
  - state file:      {state_path()}
  - contact sheet:   python3 {app_scripts_dir() / 'contact_sheet.py'} --job {jid}

The ledger above is scoped to THIS library and lists every metaphor already
spent in it. Read it before you diverge and append to it before you finish: an
icon that repeats a silhouette already in the library is a defect, even if the
icon is good on its own.

Keep job {jid} in the state file current as you go: set `status` to concepts,
drawing, proofing, then done (or failed with a `note`), and fill `icons` with
one entry per shipped file: {{"name", "file", "metaphor"}} where `file` is
relative to the workspace directory.{notes}
"""


def library_ledger_path(lib_id: str) -> Path:
    """Per-library ledger. Consistency is a within-library property, so the
    ledger has to be too -- one global ledger would make two unrelated icon sets
    compete for the same metaphors."""
    return workspace_dir() / "ledgers" / f"{_safe_id(lib_id)}.md"


def library_icons(lib_id: str) -> list[dict[str, Any]]:
    """Every icon shipped in a library, newest job first, with sanitized markup.

    The markup is inlined by the UI (an <img> cannot resolve ``currentColor``,
    which is the house default), so it is sanitized here -- see
    :func:`sanitize_svg` for why that is not optional.
    """
    state = load_state()
    root = workspace_dir()
    out: list[dict[str, Any]] = []
    for job in state.get("jobs", []):
        if job.get("libraryId") != lib_id:
            continue
        for icon in job.get("icons") or []:
            rel = str(icon.get("file") or "")
            svg = ""
            if rel:
                try:
                    path = (root / rel).resolve()
                    path.relative_to(root.resolve())
                    svg = sanitize_svg(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    svg = ""
            out.append(
                {
                    "name": str(icon.get("name") or ""),
                    "metaphor": str(icon.get("metaphor") or ""),
                    "file": rel,
                    "jobId": job.get("id", ""),
                    "svg": svg,
                }
            )
    return out


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


def _library_record(name: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _new_library_id(),
        "name": name,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": normalize_library_params(params or {}),
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
