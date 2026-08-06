"""Tests for library state, legacy migration, and SVG sanitization.

Runs standalone (``python3 test/test_store.py``) so the repo needs no test
dependency, and under pytest if you have it. Every test gets its own
``KIROCREW_HOME`` so nothing touches the real workspace.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_store():
    """Import ``backend.store`` bound to a throwaway data home."""
    tmp = tempfile.mkdtemp(prefix="icon-studio-test-")
    os.environ["KIROCREW_HOME"] = tmp
    for mod in ("backend.store", "backend.contact_sheet"):
        sys.modules.pop(mod, None)
    from backend import store

    return store, Path(tmp)


# --- library parameters --------------------------------------------------------


def test_library_params_clamp_and_default_sizes():
    store, _ = _fresh_store()
    p = store.normalize_library_params({"canvas": 4, "stroke": 99, "style": "filled"})
    assert p["canvas"] == 8, "canvas clamps to the low bound"
    assert p["stroke"] == 8.0, "stroke clamps to the high bound"
    assert p["style"] == "filled"
    assert p["keyline"] == "square", "keyline defaults"
    assert p["sizes"] == [8, 24, 32], "sizes derive from canvas plus 24/32"


def test_library_params_reject_bad_enums():
    store, _ = _fresh_store()
    for bad in ({"style": "duotone"}, {"keyline": "hexagon"}):
        try:
            store.normalize_library_params(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")


def test_job_fields_require_names_and_cap_count():
    store, _ = _fresh_store()
    try:
        store.normalize_job_fields({"names": "  ,  \n "})
        raise AssertionError("expected ValueError for empty names")
    except ValueError:
        pass

    fields = store.normalize_job_fields({"names": "star, fire\nfolder"})
    assert fields["names"] == ["star", "fire", "folder"], "splits on comma and newline"
    assert fields["mode"] == "concepts", "mode defaults"

    try:
        store.normalize_job_fields({"names": [f"i{n}" for n in range(store.MAX_NAMES + 1)]})
        raise AssertionError("expected ValueError over MAX_NAMES")
    except ValueError:
        pass


def test_library_params_win_over_job_payload():
    """The consistency guarantee: a request cannot smuggle its own canvas."""
    store, _ = _fresh_store()
    lib = store.new_library("Product", {"canvas": 24, "stroke": 2, "style": "filled"})
    fields = store.normalize_job_fields({"names": "star", "canvas": 512, "style": "outline"})
    job = store.new_job(lib, fields)
    assert job["params"]["canvas"] == 24, "library canvas wins"
    assert job["params"]["style"] == "filled", "library style wins"
    assert job["params"]["stroke"] == 2.0
    assert job["libraryId"] == lib["id"]


# --- state, counts, ledgers ----------------------------------------------------


def test_new_library_seeds_its_own_ledger():
    store, _ = _fresh_store()
    a = store.new_library("Set A", {})
    b = store.new_library("Set B", {})
    pa, pb = store.library_ledger_path(a["id"]), store.library_ledger_path(b["id"])
    assert pa.is_file() and pb.is_file(), "each library gets a ledger"
    assert pa != pb, "ledgers are per-library, not shared"
    assert "Set A" in pa.read_text(encoding="utf-8")


def test_public_library_counts_only_its_own_jobs():
    store, _ = _fresh_store()
    a = store.new_library("A", {})
    b = store.new_library("B", {})
    store.new_job(a, store.normalize_job_fields({"names": "one, two"}))
    jb = store.new_job(b, store.normalize_job_fields({"names": "three"}))
    store.update_job(jb["id"], icons=[{"name": "three", "file": "x.svg", "metaphor": "m"}])

    state = store.load_state()
    counts = {lib["name"]: store.public_library(state, lib) for lib in state["libraries"]}
    assert counts["A"]["jobCount"] == 1 and counts["A"]["iconCount"] == 0
    assert counts["B"]["jobCount"] == 1 and counts["B"]["iconCount"] == 1


def test_update_library_does_not_retro_edit_finished_jobs():
    store, _ = _fresh_store()
    lib = store.new_library("L", {"canvas": 16})
    job = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    store.update_library(lib["id"], params={"canvas": 32})
    assert store.get_job(job["id"])["params"]["canvas"] == 16, "history is a record, not a reference"
    assert store.get_library(lib["id"])["params"]["canvas"] == 32


def test_migration_adopts_legacy_jobs_into_a_library():
    """Pre-library state has jobs and no libraries. Nothing may be dropped."""
    store, _ = _fresh_store()
    store.ensure_workspace()
    legacy = {
        "jobs": [
            {
                "id": "20260101-001",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "status": "done",
                "params": {
                    "names": ["star"],
                    "canvas": 24,
                    "sizes": [24, 32],
                    "stroke": 1.5,
                    "style": "filled",
                    "keyline": "circle",
                    "mode": "ship",
                    "notes": "",
                },
                "icons": [],
                "proof": "",
                "note": "",
            }
        ]
    }
    store.state_path().write_text(json.dumps(legacy), encoding="utf-8")

    state = store.load_state()
    assert len(state["libraries"]) == 1, "a library is seeded"
    lib = state["libraries"][0]
    assert state["jobs"][0]["libraryId"] == lib["id"], "the orphan job is adopted"
    assert lib["params"]["canvas"] == 24, "seeded from the job's own params, not a default"
    assert lib["params"]["keyline"] == "circle"
    assert store.load_state()["libraries"][0]["id"] == lib["id"], "migration is stable"


def test_migration_is_a_noop_on_empty_and_current_state():
    store, _ = _fresh_store()
    assert store.load_state()["libraries"] == [], "no libraries invented for empty state"
    lib = store.new_library("L", {})
    store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    before = store.state_path().read_text(encoding="utf-8")
    store.load_state()
    assert store.state_path().read_text(encoding="utf-8") == before, "no rewrite churn"


def test_library_created_from_a_name_alone_gets_the_house_spec():
    """Creation asks only for a name, so the defaults must be the house spec."""
    store, _ = _fresh_store()
    lib = store.new_library("Product", {})
    assert lib["name"] == "Product"
    assert lib["params"] == {
        "canvas": 16,
        "sizes": [16, 24, 32],
        "stroke": 1.0,
        "style": "outline",
        "keyline": "square",
    }


def test_library_name_is_still_required():
    store, _ = _fresh_store()
    for bad in ("", "   ", None):
        try:
            store.new_library(bad, {})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for name={bad!r}")


# --- redraw --------------------------------------------------------------------


def _ship(store, lib, pairs):
    """Create a job and mark the given (name, metaphor) pairs as shipped."""
    job = store.new_job(lib, store.normalize_job_fields({"names": [n for n, _ in pairs]}))
    store.update_job(
        job["id"],
        status="done",
        icons=[
            {"name": n, "file": f"icons/{job['id']}/{i}.svg", "metaphor": m}
            for i, (n, m) in enumerate(pairs)
        ],
    )
    return job


def test_redraw_roster_is_shipped_icons_deduped_newest_first():
    store, _ = _fresh_store()
    lib = store.new_library("L", {})
    _ship(store, lib, [("star", "burst"), ("fire", "ember")])
    _ship(store, lib, [("star", "burst v2"), ("folder", "corner")])
    # Requested but never shipped -- has no ledger entry, so nothing to preserve.
    store.new_job(lib, store.normalize_job_fields({"names": "ghost"}))

    names = store.library_icon_names(lib["id"])
    assert names == ["star", "folder", "fire"], names
    assert "ghost" not in names, "unshipped names are not redrawn"


def test_redraw_roster_is_scoped_to_one_library():
    store, _ = _fresh_store()
    a, b = store.new_library("A", {}), store.new_library("B", {})
    _ship(store, a, [("star", "burst")])
    _ship(store, b, [("fire", "ember")])
    assert store.library_icon_names(a["id"]) == ["star"]
    assert store.library_icon_names(b["id"]) == ["fire"]


def test_redraw_job_forces_ship_mode_and_skips_the_name_cap():
    store, _ = _fresh_store()
    many = [f"icon-{n}" for n in range(store.MAX_NAMES + 5)]
    fields = store.normalize_job_fields({"names": many, "kind": "redraw", "mode": "concepts"})
    assert fields["kind"] == "redraw"
    assert fields["mode"] == "ship", "a redraw never stops to propose metaphors"
    assert len(fields["names"]) == store.MAX_NAMES + 5, "cap does not apply to a redraw"

    # The cap still applies to a fresh request.
    try:
        store.normalize_job_fields({"names": many, "kind": "new"})
        raise AssertionError("expected ValueError over MAX_NAMES for a new request")
    except ValueError:
        pass


def test_redraw_job_kind_is_validated():
    store, _ = _fresh_store()
    try:
        store.normalize_job_fields({"names": "star", "kind": "obliterate"})
        raise AssertionError("expected ValueError for an unknown kind")
    except ValueError:
        pass


def test_redraw_brief_forbids_diverging_and_new_brief_requires_it():
    store, _ = _fresh_store()
    lib = store.new_library("L", {"canvas": 24, "stroke": 2})
    redraw = store.new_job(
        lib, store.normalize_job_fields({"names": ["star", "fire"], "kind": "redraw"})
    )
    text = store.compose_brief(redraw, lib)
    assert "REDRAW" in text
    assert "Do NOT diverge" in text, "a redraw must not re-invent metaphors"
    assert "Icons to redraw (2)" in text
    assert "reproduce its metaphor" in text
    assert "24x24" in text and "2.0px" in text, "redraw carries the NEW parameters"
    assert "CONCEPTS" not in text and "three metaphors" not in text

    fresh = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    fresh_text = store.compose_brief(fresh, lib)
    assert "CONCEPTS" in fresh_text
    assert "Do NOT diverge" not in fresh_text
    assert "Icons requested (1)" in fresh_text


def test_redraw_uses_the_libraries_current_params_not_the_originals():
    """The point of a redraw: bring old icons up to the new spec."""
    store, _ = _fresh_store()
    lib = store.new_library("L", {"canvas": 16, "stroke": 1})
    _ship(store, lib, [("star", "burst")])
    store.update_library(lib["id"], params={"canvas": 32, "stroke": 2})

    fresh_lib = store.get_library(lib["id"])
    names = store.library_icon_names(lib["id"])
    redraw = store.new_job(fresh_lib, store.normalize_job_fields({"names": names, "kind": "redraw"}))
    assert redraw["params"]["canvas"] == 32
    assert redraw["params"]["stroke"] == 2.0


# --- SVG sanitization ----------------------------------------------------------

_OK_SVG = '<svg viewBox="0 0 16 16"><path d="M2 2h12" stroke="currentColor"/></svg>'


def test_sanitize_keeps_a_clean_icon_and_its_currentcolor():
    store, _ = _fresh_store()
    out = store.sanitize_svg(f"<?xml version='1.0'?>\n{_OK_SVG}\n")
    assert out.startswith("<svg") and out.endswith("</svg>"), "XML prologue stripped"
    assert "currentColor" in out, "house default colour survives"


def test_sanitize_rejects_executable_and_external_content():
    store, _ = _fresh_store()
    hostile = [
        '<svg><script>fetch("/api/chat")</script></svg>',
        '<svg onload="alert(1)"><path d="M0 0"/></svg>',
        '<svg><path onclick="steal()" d="M0 0"/></svg>',
        '<svg><foreignObject><body>x</body></foreignObject></svg>',
        '<svg><image href="https://evil.test/x.png"/></svg>',
        '<svg><a href="javascript:alert(1)">x</a></svg>',
        '<svg><style>@import url(https://evil.test/x.css)</style></svg>',
        '<svg><use href="https://evil.test/x#y"/></svg>',
        "not an svg at all",
        "<svg>" + "x" * 70_000 + "</svg>",
    ]
    for markup in hostile:
        assert store.sanitize_svg(markup) == "", f"should be rejected: {markup[:48]}"


def test_sanitize_allows_internal_fragment_refs():
    store, _ = _fresh_store()
    markup = '<svg><clipPath id="c"><rect/></clipPath><g clip-path="url(#c)"><path d="M0 0"/></g></svg>'
    assert store.sanitize_svg(markup).startswith("<svg"), "in-document refs are fine"


def test_library_icons_reads_and_sanitizes_from_disk():
    store, home = _fresh_store()
    lib = store.new_library("L", {})
    job = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    svg_path = Path(lib["outputPath"]) / "star.svg"
    svg_path.write_text(_OK_SVG, encoding="utf-8")
    store.update_job(
        job["id"], icons=[{"name": "star", "file": "star.svg", "metaphor": "burst"}]
    )

    icons = store.library_icons(lib["id"])
    assert len(icons) == 1
    assert icons[0]["name"] == "star" and icons[0]["jobId"] == job["id"]
    assert "currentColor" in icons[0]["svg"]


def test_library_icons_refuses_paths_outside_the_workspace():
    """A traversing ``file`` field must not become an arbitrary file read."""
    store, home = _fresh_store()
    secret = home / "secret.svg"
    secret.write_text(_OK_SVG, encoding="utf-8")
    lib = store.new_library("L", {})
    job = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    store.update_job(job["id"], icons=[{"name": "x", "file": "../../secret.svg"}])

    assert store.library_icons(lib["id"])[0]["svg"] == "", "traversal yields nothing"


def test_safe_id_rejects_traversal():
    store, _ = _fresh_store()
    for bad in ("../../etc/passwd", "..", ".", "", "/abs"):
        try:
            path = store.library_ledger_path(bad)
        except ValueError:
            continue
        assert ".." not in str(path), f"traversal leaked for {bad!r}: {path}"


# --- output folders -------------------------------------------------------------


def test_new_library_creates_a_default_output_folder_in_the_workspace():
    store, home = _fresh_store()
    lib = store.new_library("Product Icons", {})
    path = Path(lib["outputPath"])

    assert path.is_dir(), "the folder is created, not just recorded"
    assert path.name == "product-icons", "named from a slug of the library name"
    assert path.parent == store.libraries_root()
    assert str(home) in str(path), "default lives under the Kiro Crew data home"


def test_default_output_folders_do_not_collide_on_duplicate_names():
    store, _ = _fresh_store()
    first = store.new_library("Icons", {})
    second = store.new_library("Icons", {})
    assert first["outputPath"] != second["outputPath"]
    assert Path(second["outputPath"]).name == "icons-2"


def test_output_path_accepts_an_arbitrary_local_folder():
    store, _ = _fresh_store()
    target = Path(tempfile.mkdtemp(prefix="icon-studio-dest-")) / "my-icons"
    lib = store.new_library("L", {})
    updated = store.update_library(lib["id"], outputPath=str(target))

    assert updated["outputPath"] == str(target.resolve())
    assert target.is_dir(), "the chosen folder is created if missing"


def test_output_path_rejects_protected_and_relative_locations():
    store, home = _fresh_store()
    lib = store.new_library("L", {})
    original = lib["outputPath"]
    bad = [
        "relative/path",  # not absolute
        "~/.ssh",  # credentials
        "~/.aws/cli",  # credentials, nested
        "/etc",  # system
        "/",  # filesystem root
        str(home / "agents"),  # inside the data home, outside the workspace
        str(home / "apps" / "icon-studio"),  # the app's own code
    ]
    for candidate in bad:
        try:
            store.update_library(lib["id"], outputPath=candidate)
        except ValueError:
            continue
        raise AssertionError(f"accepted an unsafe output folder: {candidate!r}")

    assert store.get_library(lib["id"])["outputPath"] == original, "unchanged after refusals"


def test_output_path_rejects_a_file_and_a_git_dir():
    store, _ = _fresh_store()
    tmp = Path(tempfile.mkdtemp(prefix="icon-studio-dest-"))
    a_file = tmp / "notes.txt"
    a_file.write_text("x", encoding="utf-8")
    lib = store.new_library("L", {})

    for candidate in (str(a_file), str(tmp / ".git" / "hooks")):
        try:
            store.update_library(lib["id"], outputPath=candidate)
        except ValueError:
            continue
        raise AssertionError(f"accepted {candidate!r}")


def test_changing_the_output_path_copies_existing_icons_and_keeps_the_originals():
    """The gallery reads from the current folder, so a repoint must not orphan icons."""
    store, _ = _fresh_store()
    lib = store.new_library("L", {})
    old = Path(lib["outputPath"])
    (old / "star.svg").write_text(_OK_SVG, encoding="utf-8")

    target = Path(tempfile.mkdtemp(prefix="icon-studio-dest-")) / "moved"
    store.update_library(lib["id"], outputPath=str(target))

    assert (target / "star.svg").is_file(), "icon followed the library"
    assert (old / "star.svg").is_file(), "the only copy is never deleted"


def test_brief_points_the_agent_at_the_library_folder():
    store, _ = _fresh_store()
    target = Path(tempfile.mkdtemp(prefix="icon-studio-dest-")) / "briefdir"
    lib = store.new_library("L", {})
    store.update_library(lib["id"], outputPath=str(target))
    lib = store.get_library(lib["id"])

    job = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    brief = store.compose_brief(job, lib)

    assert str(target.resolve()) in brief
    assert "bare file name" in brief, "the agent is told what to record in `file`"
    assert "no per-request subfolder" in brief


def test_icons_resolve_from_the_library_folder_and_the_legacy_location():
    store, _ = _fresh_store()
    lib = store.new_library("L", {})
    out_dir = Path(lib["outputPath"])
    (out_dir / "star.svg").write_text(_OK_SVG, encoding="utf-8")

    # Legacy: a pre-output-path job recorded a workspace-relative path.
    legacy = store.workspace_dir() / "icons" / "old"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "bell.svg").write_text(_OK_SVG, encoding="utf-8")

    job = store.new_job(lib, store.normalize_job_fields({"names": "star\nbell"}))
    store.update_job(
        job["id"],
        icons=[
            {"name": "star", "file": "star.svg"},
            {"name": "bell", "file": "icons/old/bell.svg"},
        ],
    )

    icons = {i["name"]: i["svg"] for i in store.library_icons(lib["id"])}
    assert "currentColor" in icons["star"], "read from the library folder"
    assert "currentColor" in icons["bell"], "legacy workspace-relative path still resolves"


def test_icon_file_field_cannot_escape_the_library_folder():
    store, _ = _fresh_store()
    outside = Path(tempfile.mkdtemp(prefix="icon-studio-secret-")) / "secret.svg"
    outside.write_text(_OK_SVG, encoding="utf-8")
    lib = store.new_library("L", {})
    job = store.new_job(lib, store.normalize_job_fields({"names": "star"}))
    store.update_job(job["id"], icons=[{"name": "x", "file": str(outside)}])

    assert store.library_icons(lib["id"])[0]["svg"] == "", "absolute escape yields nothing"


def test_contact_sheet_filters_to_one_job_within_a_shared_folder():
    """A library folder is flat and shared, so a per-job sheet must filter."""
    store, _ = _fresh_store()
    from backend import contact_sheet

    lib = store.new_library("L", {})
    out_dir = Path(lib["outputPath"])
    for name in ("star", "bell", "folder"):
        (out_dir / f"{name}.svg").write_text(_OK_SVG, encoding="utf-8")

    everything = contact_sheet.collect_icons(out_dir)
    just_one = contact_sheet.collect_icons(out_dir, ["bell"])

    assert [n for n, _ in everything] == ["bell", "folder", "star"]
    assert [n for n, _ in just_one] == ["bell"]


# --- host integration ----------------------------------------------------------


def test_routes_module_loads_the_way_the_gateway_loads_it():
    """Reproduce ``kiro_crew.apps.module_loader.load_app_module`` exactly.

    This is the test whose absence let a completely dead app pass 23 green ones.
    The host loads ``backend/routes.py`` from a file path under the synthetic name
    ``_kirocrew_app_icon-studio.backend.routes`` WITHOUT creating the parent
    packages, so a plain ``from . import store`` raises ModuleNotFoundError, the
    RouteRegistry registers zero routes, and every API call 404s with
    ``{"error": "not found"}`` while the app still looks installed and enabled.

    Skipped when aiohttp/kiro_crew are absent, which is the case for a plain
    ``python3 test/test_store.py`` outside the gateway's interpreter.
    """
    import importlib.util

    if importlib.util.find_spec("aiohttp") is None:
        print("    (skipped: aiohttp not importable)")
        return
    if importlib.util.find_spec("kiro_crew") is None:
        print("    (skipped: kiro_crew not importable)")
        return

    app_root = Path(__file__).resolve().parents[1]
    unique = "_kirocrew_app_icon-studio.backend.routes"
    for key in [k for k in sys.modules if k.startswith("_kirocrew_app_icon-studio")]:
        del sys.modules[key]

    spec = importlib.util.spec_from_file_location(unique, str(app_root / "backend/routes.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique] = module
    try:
        spec.loader.exec_module(module)  # this is the line that used to fail
        register = getattr(module, "register_routes", None)
        assert callable(register), "the hook the manifest names must exist and be callable"
    finally:
        for key in [k for k in sys.modules if k.startswith("_kirocrew_app_icon-studio")]:
            del sys.modules[key]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001 - test harness reports everything
            failed += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
