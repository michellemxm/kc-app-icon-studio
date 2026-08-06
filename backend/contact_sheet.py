"""Contact-sheet renderer for Icon Studio.

Pure standalone module: imports nothing from ``kiro_crew``, so the app backend
(``backend.routes``) and the agent-facing CLI (``scripts/contact_sheet.py``)
can both use it. Everything here is filesystem + subprocess work.

The sheet is rendered twice on purpose:

* ``@1x`` — the true pixel grid. This is the one that answers "does this 1px
  stroke land on a pixel or smear across two", which is the whole reason the
  proof step exists.
* ``@2x`` — what a Retina user actually sees.

Icons are INLINED into the HTML rather than referenced with ``<img>``, because
``stroke="currentColor"`` only inherits when the SVG is part of the host
document. An ``<img>`` would render every icon black and the dark band would be
useless.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "icon-studio"

# --- layout constants (px, CSS) -------------------------------------------------
PAGE_PAD = 16
BAND_PAD = 14
BAND_TITLE_H = 20
BAND_GAP = 12
ROW_LABEL_H = 18
ROW_GAP = 8
CELL_W = 76
CELL_H = 66
MAX_COLS = 8
MIN_WIDTH = 420

#: Marker the page writes into <title> so the height can be read back.
MEASURE_TOKEN = "ICON_STUDIO_H:"

_XML_DECL_RE = re.compile(r"<\?xml[^>]*\?>", re.I)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.I)

_BANDS = (
    ("Light", "#ffffff", "#111318", "#6b7280"),
    ("Dark", "#111318", "#e6e8ee", "#9aa3b2"),
)


class RenderError(RuntimeError):
    """Raised when the sheet cannot be produced (no icons, or no browser)."""


@dataclass
class Sheet:
    png_1x: Path
    png_2x: Path
    width: int
    height: int
    icon_count: int
    sizes: list[int]
    browser: str

    def to_dict(self) -> dict:
        return {
            "png": str(self.png_1x),
            "png2x": str(self.png_2x),
            "width": self.width,
            "height": self.height,
            "iconCount": self.icon_count,
            "sizes": self.sizes,
            "browser": self.browser,
        }


# --- paths ---------------------------------------------------------------------


def data_home() -> Path:
    """Kiro Crew data home. Honours the same env overrides the gateway does."""
    for env in ("KIROCREW_HOME", "KIRO_CREW_HOME"):
        raw = os.environ.get(env)
        if raw:
            return Path(raw).expanduser()
    kiro_home = os.environ.get("KIRO_HOME")
    if kiro_home:
        return Path(kiro_home).expanduser() / "crew"
    return Path.home() / ".kiro" / "crew"


def workspace_dir() -> Path:
    return data_home() / "workspace" / APP_NAME


def app_dir() -> Path:
    return data_home() / "apps" / APP_NAME


def job_dir(job_id: str) -> Path:
    safe = _safe_component(job_id)
    return workspace_dir() / "icons" / safe


def proof_path(job_id: str, scale: int = 1) -> Path:
    safe = _safe_component(job_id)
    suffix = "" if scale == 1 else f"@{scale}x"
    return workspace_dir() / "proofs" / f"{safe}{suffix}.png"


def ledger_path() -> Path:
    return workspace_dir() / "metaphor-ledger.md"


def state_path() -> Path:
    return workspace_dir() / "state.json"


def _safe_component(value: str) -> str:
    """Reject anything that could escape the workspace when used as a path part."""
    if not isinstance(value, str) or not value.strip():
        raise RenderError("job id is empty")
    if any(ch in value for ch in ("/", "\\", "\x00")) or value in (".", ".."):
        raise RenderError(f"unsafe job id: {value!r}")
    return value.strip()


# --- browser discovery ---------------------------------------------------------

_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_MAC_CHROMIUM = "/Applications/Chromium.app/Contents/MacOS/Chromium"


def find_browser() -> str | None:
    """First usable Chrome/Chromium, or None.

    Order: explicit override, the Playwright browser cache (present on any
    machine that has ever run the dashboard's browser tooling), then a
    system install.
    """
    override = os.environ.get("ICON_STUDIO_CHROME")
    if override and Path(override).is_file():
        return override

    for candidate in _playwright_candidates():
        if candidate.is_file():
            return str(candidate)

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found

    for path in (_MAC_CHROME, _MAC_CHROMIUM):
        if Path(path).is_file():
            return path
    return None


def _playwright_candidates() -> list[Path]:
    roots = [
        Path.home() / "Library" / "Caches" / "ms-playwright",  # macOS
        Path.home() / ".cache" / "ms-playwright",  # Linux
    ]
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        # Newest build first — directory names are chromium-<build>.
        try:
            builds = sorted(
                (d for d in root.iterdir() if d.is_dir() and d.name.startswith("chromium-")),
                key=lambda d: d.name,
                reverse=True,
            )
        except OSError:
            continue
        for build in builds:
            out.extend(
                [
                    build
                    / "chrome-mac-arm64"
                    / "Google Chrome for Testing.app"
                    / "Contents"
                    / "MacOS"
                    / "Google Chrome for Testing",
                    build
                    / "chrome-mac"
                    / "Google Chrome for Testing.app"
                    / "Contents"
                    / "MacOS"
                    / "Google Chrome for Testing",
                    build / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
                    build / "chrome-linux" / "chrome",
                ]
            )
    return out


# --- html ----------------------------------------------------------------------


def _clean_svg(text: str) -> str:
    text = _XML_DECL_RE.sub("", text)
    text = _DOCTYPE_RE.sub("", text)
    return text.strip()


def collect_icons(directory: Path, names: list[str] | None = None) -> list[tuple[str, str]]:
    """(name, inline svg markup) for .svg files in *directory*, sorted by name.

    *names* restricts the sheet to those icon stems. A library's folder is flat
    and shared by every request in it, so a per-request sheet has to filter or it
    would proof the whole library and quietly claim icons it never drew.
    """
    if not directory.is_dir():
        raise RenderError(f"no such icon directory: {directory}")
    wanted = {str(n).strip() for n in names} if names else None
    icons: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.svg")):
        if wanted is not None and path.stem not in wanted:
            continue
        try:
            icons.append((path.stem, _clean_svg(path.read_text(encoding="utf-8"))))
        except OSError as exc:
            raise RenderError(f"could not read {path}: {exc}") from exc
    if not icons:
        scope = " matching this request" if wanted else ""
        raise RenderError(f"no .svg files{scope} in {directory}")
    return icons


def build_html(icons: list[tuple[str, str]], sizes: list[int], title: str) -> tuple[str, int]:
    """Return (html, width). The HEIGHT is deliberately not computed here.

    An earlier version pre-computed the page height so the window could be sized
    to match. It was wrong by 71px on the second band, which pushed the last row
    of icons off its dark background and onto the light page — light strokes on
    light grey, invisible, in the one artefact whose entire job is to show you
    what the icons look like. Nothing asserts a height now: the page sizes itself
    and :func:`_measure` reads back ``scrollHeight``. Width stays computed because
    it derives from fixed cell widths, not from flow.
    """
    cols = min(MAX_COLS, len(icons))
    inner_w = max(cols * CELL_W, MIN_WIDTH - 2 * (PAGE_PAD + BAND_PAD))
    width = inner_w + 2 * (PAGE_PAD + BAND_PAD)

    bands = "\n".join(
        _band_html(label, bg, fg, muted, icons, sizes, inner_w)
        for label, bg, fg, muted in _BANDS
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    width: {width}px;
    padding: {PAGE_PAD}px;
    background: #f3f4f6;
    font: 400 11px/1.2 -apple-system, "Segoe UI", system-ui, sans-serif;
  }}
  .band {{ padding: {BAND_PAD}px; border-radius: 8px; overflow: hidden; }}
  .band + .band {{ margin-top: {BAND_GAP}px; }}
  .band-title {{ height: {BAND_TITLE_H}px; font-size: 11px; font-weight: 600;
                 letter-spacing: .06em; text-transform: uppercase; }}
  .row + .row {{ margin-top: {ROW_GAP}px; }}
  .row-label {{ height: {ROW_LABEL_H}px; font-size: 10px; letter-spacing: .04em; }}
  .cells {{ display: flex; flex-wrap: wrap; width: {inner_w}px; }}
  .cell {{ width: {CELL_W}px; height: {CELL_H}px;
           display: flex; flex-direction: column; align-items: center;
           justify-content: flex-start; padding-top: 6px; }}
  .art {{ height: 40px; display: flex; align-items: center; justify-content: center; }}
  /* Force the size from the wrapper, so an icon that ships width="16" on its
     root still renders at 24 and 32 instead of collapsing every row to 16. */
  .art svg {{ display: block; width: 100%; height: 100%; }}
  .name {{ margin-top: 4px; max-width: {CELL_W - 8}px; font-size: 9px;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
</style></head>
<body>
{bands}
<script>document.title = {MEASURE_TOKEN!r} + document.documentElement.scrollHeight
  + ':' + window.innerHeight;</script>
</body></html>
"""
    return html, width


def _band_html(
    label: str,
    bg: str,
    fg: str,
    muted: str,
    icons: list[tuple[str, str]],
    sizes: list[int],
    inner_w: int,
) -> str:
    rows = []
    for size in sizes:
        cells = "\n".join(
            f'<div class="cell"><div class="art" style="color:{fg}">'
            f'<span style="display:block;width:{size}px;height:{size}px">{svg}</span>'
            f'</div><div class="name" style="color:{muted}">{_esc(name)}</div></div>'
            for name, svg in icons
        )
        rows.append(
            f'<div class="row"><div class="row-label" style="color:{muted}">{size}px</div>'
            f'<div class="cells">{cells}</div></div>'
        )
    return (
        f'<div class="band" style="background:{bg}">'
        f'<div class="band-title" style="color:{muted}">{_esc(label)}</div>'
        f"{''.join(rows)}</div>"
    )


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --- rasterize -----------------------------------------------------------------


def _render_lock():
    """Serialize browser invocations across processes.

    Without ``--user-data-dir`` (see :func:`_shoot`) every render shares Chrome's
    default profile, so two overlapping runs — the agent's CLI and the UI's
    Re-proof button — would fight over the singleton and one would come back with
    no PNG. A lock file is cheaper and more predictable than a private profile.
    """
    lock_path = workspace_dir() / ".render.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")  # noqa: SIM115 — closed by the caller's finally
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass  # Advisory only: an unlockable filesystem must not block rendering.
    return handle


def _measure(browser: str, html_file: Path, width: int) -> tuple[int, int]:
    """Return (content height, window-chrome inset), both in CSS px.

    ``--dump-dom`` runs scripts and prints the resulting DOM, so the page reports
    two numbers in its title:

    * ``scrollHeight`` — how tall the sheet actually is. Computing this in Python
      is what produced an invisible bottom row once already.
    * ``innerHeight`` — the real viewport for a known ``--window-size``. Under
      ``--headless=new`` on macOS the window size INCLUDES browser chrome (87px
      measured here), so a window sized to the content leaves the last 87px of
      the sheet unpainted — the sheet looks complete and silently is not. The
      inset is measured rather than hardcoded because it differs per platform.
    """
    probe_h = 600
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-sandbox",
        "--no-first-run",
        "--disable-extensions",
        "--disable-dev-shm-usage",
        f"--window-size={width},{probe_h}",
        "--dump-dom",
        html_file.resolve().as_uri(),
    ]
    lock = _render_lock()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("headless browser timed out measuring the sheet") from exc
    finally:
        lock.close()

    match = re.search(rf"{re.escape(MEASURE_TOKEN)}(\d+):(\d+)", proc.stdout or "")
    if not match:
        raise RenderError("could not measure the sheet height (no token in dumped DOM)")
    height = int(match.group(1))
    inner = int(match.group(2))
    if not 100 <= height <= 20000:
        raise RenderError(f"implausible sheet height: {height}px")
    inset = max(0, probe_h - inner)
    if inset > 400:  # nothing sane has 400px of window chrome
        inset = 0
    return height, inset


def _shoot(
    browser: str,
    html_file: Path,
    out: Path,
    width: int,
    height: int,
    scale: int,
    inset: int = 0,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-sandbox",
        "--no-first-run",
        "--disable-extensions",
        "--disable-dev-shm-usage",
        f"--force-device-scale-factor={scale}",
        f"--window-size={width},{height + inset}",
        # Two flags deliberately ABSENT, both verified to hang --headless=new
        # indefinitely on Chrome for Testing (macOS arm64, build 1208):
        #   --user-data-dir=<anything>  (hangs even on a reused directory)
        #   --virtual-time-budget=<ms>  (never returns for a static page)
        # The profile is therefore shared, which is why _render_lock exists.
        f"--screenshot={out}",
        html_file.resolve().as_uri(),
    ]
    lock = _render_lock()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"headless browser timed out after 60s rendering {out.name}") from exc
    finally:
        lock.close()
    if not out.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RenderError(f"headless browser produced no PNG: {detail or 'no output'}")


def render_dir(
    directory: Path,
    out_1x: Path,
    out_2x: Path,
    sizes: list[int] | None = None,
    title: str = "Contact sheet",
    names: list[str] | None = None,
) -> Sheet:
    """Render the SVGs in *directory* into a light/dark contact sheet.

    *names* narrows it to specific icon stems -- see :func:`collect_icons`.
    """
    icons = collect_icons(directory, names)
    sizes = sorted({int(s) for s in (sizes or [16, 24, 32]) if int(s) > 0})
    if not sizes:
        raise RenderError("no valid sizes requested")

    browser = find_browser()
    if browser is None:
        raise RenderError(
            "no Chrome/Chromium found, so the icons cannot be proven at real size. "
            "Install one with `npx playwright install chromium`, or set "
            "ICON_STUDIO_CHROME to a browser binary."
        )

    html, width = build_html(icons, sizes, title)
    with tempfile.TemporaryDirectory(prefix="icon-studio-sheet-") as tmp:
        html_file = Path(tmp) / "sheet.html"
        html_file.write_text(html, encoding="utf-8")
        height, inset = _measure(browser, html_file, width)
        _shoot(browser, html_file, out_1x, width, height, 1, inset)
        _shoot(browser, html_file, out_2x, width, height, 2, inset)

    return Sheet(
        png_1x=out_1x,
        png_2x=out_2x,
        width=width,
        height=height,
        icon_count=len(icons),
        sizes=sizes,
        browser=browser,
    )


def render_job(job_id: str, sizes: list[int] | None = None) -> Sheet:
    """Render a job's sheet from the LEGACY per-job icon directory.

    Kept for pre-library job directories and ad-hoc use. Current jobs write into
    their library's output folder, which only ``store`` can resolve -- use
    ``store.render_job_sheet`` for those.
    """
    return render_dir(
        job_dir(job_id),
        proof_path(job_id, 1),
        proof_path(job_id, 2),
        sizes=sizes,
        title=f"Icon Studio — {job_id}",
    )
