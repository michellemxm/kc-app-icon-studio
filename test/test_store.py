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
    svg_path = store.job_dir(job["id"]) / "star.svg"
    svg_path.write_text(_OK_SVG, encoding="utf-8")
    rel = str(svg_path).replace(str(store.workspace_dir()) + "/", "")
    store.update_job(job["id"], icons=[{"name": "star", "file": rel, "metaphor": "burst"}])

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
