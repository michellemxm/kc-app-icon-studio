"""Icon Studio backend routes (external-app contract).

``register_routes(ctx)`` returns a list of :class:`AppRoute` with paths RELATIVE
to ``/api/apps/icon-studio``. Handlers take ``(request, ctx)``. Do NOT register
directly on the aiohttp router here — the RouteRegistry catch-all shadows it for
external apps, and the handlers would never dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from kiro_crew.apps.route_registry import AppRoute


def _bootstrap_package() -> None:
    """Make relative imports work inside an EXTERNAL app's backend package.

    The host loads this file with ``importlib.util.spec_from_file_location``
    under the synthetic name ``_kirocrew_app_icon-studio.backend.routes`` and
    does NOT create the parent packages that name implies. Relative imports
    resolve through ``sys.modules[__package__]``, so ``from . import store``
    dies with::

        ModuleNotFoundError: No module named '_kirocrew_app_icon-studio'

    which the RouteRegistry catches, logs, and turns into ZERO registered
    routes -- after which every request to /api/apps/icon-studio 404s with the
    generic ``{"error": "not found"}``. The app looks installed and enabled and
    is completely dead.

    Builtin apps get away with relative imports because they genuinely live
    inside the ``kiro_crew`` package. External apps do not, so register the
    missing parents ourselves with ``__path__`` pointing at this directory.

    Reusing the host's own naming is deliberate: it sweeps ``sys.modules`` by
    the ``_kirocrew_app_<name>.`` prefix on disable, so these synthetic entries
    are unloaded with the rest and a re-enable picks up fresh code.
    """
    import sys
    import types
    from pathlib import Path

    parts = __name__.split(".")
    if len(parts) < 2:
        # Imported normally (tests, scripts) -- real packages already exist.
        return
    here = str(Path(__file__).resolve().parent)
    for depth in range(1, len(parts)):
        name = ".".join(parts[:depth])
        if name in sys.modules:
            continue
        pkg = types.ModuleType(name)
        pkg.__path__ = [here]  # type: ignore[attr-defined]
        sys.modules[name] = pkg


_bootstrap_package()

from . import store  # noqa: E402
from .contact_sheet import (  # noqa: E402
    RenderError,
    find_browser,
    proof_path,
    workspace_dir,
)
from .reveal import RevealError, reveal_binary, reveal_folder  # noqa: E402

logger = logging.getLogger(__name__)

APP_NAME = "icon-studio"


def register_routes(ctx: Any) -> list[AppRoute]:
    store.ensure_workspace()
    return [
        AppRoute("GET", "/health", health),
        AppRoute("GET", "/state", get_state),
        AppRoute("POST", "/libraries", create_library),
        AppRoute("PATCH", "/libraries/{lib_id}", patch_library),
        AppRoute("GET", "/libraries/{lib_id}/icons", get_library_icons),
        AppRoute("POST", "/libraries/{lib_id}/redraw", redraw_library),
        AppRoute("POST", "/libraries/{lib_id}/reveal", reveal_library_folder),
        AppRoute("POST", "/jobs", create_job),
        AppRoute("POST", "/jobs/{job_id}/render", render_sheet),
        AppRoute("GET", "/jobs/{job_id}/proof", get_proof),
    ]


async def _json_body(request: web.Request) -> dict[str, Any]:
    """Parse an object body or raise the 400 the caller should return."""
    try:
        body = await request.json()
    except Exception:
        raise ValueError("invalid JSON")
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    return body


def _unauthorized(request: web.Request) -> web.Response | None:
    if request.get("user") is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def health(request: web.Request, ctx: Any) -> web.Response:
    denied = _unauthorized(request)
    if denied:
        return denied
    browser = await asyncio.to_thread(find_browser)
    return web.json_response(
        {
            "ok": True,
            "app": APP_NAME,
            "workspace": str(workspace_dir()),
            "browser": browser or "",
            "canProve": bool(browser),
        }
    )


async def get_state(request: web.Request, ctx: Any) -> web.Response:
    denied = _unauthorized(request)
    if denied:
        return denied
    state = await asyncio.to_thread(store.load_state)
    jobs = [store.public_job(j) for j in state.get("jobs", [])]
    libraries = [store.public_library(state, lib) for lib in state.get("libraries", [])]
    return web.json_response({"libraries": libraries, "jobs": jobs})


async def create_library(request: web.Request, ctx: Any) -> web.Response:
    """Create a library from a name alone.

    Parameters are NOT asked for here. They default to the house set and are
    revisable later via PATCH, because a library's spec is a decision the user
    refines once there are icons to look at -- not a gate before any exist.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    try:
        body = await _json_body(request)
        params = store.normalize_library_params(body.get("params") or {})
        lib = await asyncio.to_thread(
            store.new_library, body.get("name"), params, body.get("outputPath")
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"error": f"could not create that folder: {exc}"}, status=400)
    logger.info(
        "Icon Studio: created library %s (%s) -> %s",
        lib["id"],
        lib["name"],
        lib.get("outputPath"),
    )
    state = await asyncio.to_thread(store.load_state)
    return web.json_response({"library": store.public_library(state, lib)})


async def redraw_library(request: web.Request, ctx: Any) -> web.Response:
    """Re-render every icon in a library at its current parameters.

    Returns the same ``{job, brief}`` shape as ``create_job`` so the UI
    dispatches it through one code path. The brief tells the agent to reuse each
    icon's recorded metaphor rather than diverge -- changing parameters must not
    change what the icons mean.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    lib_id = request.match_info.get("lib_id", "")
    library = await asyncio.to_thread(store.get_library, lib_id)
    if library is None:
        return web.json_response({"error": "no such library"}, status=404)

    names = await asyncio.to_thread(store.library_icon_names, lib_id)
    if not names:
        return web.json_response(
            {"error": "this library has no shipped icons to redraw yet"}, status=400
        )

    try:
        body = await _json_body(request)
    except ValueError:
        body = {}
    fields = store.normalize_job_fields(
        {"names": names, "kind": "redraw", "notes": body.get("notes")}
    )
    job = await asyncio.to_thread(store.new_job, library, fields)
    brief = store.compose_brief(job, library)
    logger.info(
        "Icon Studio: redraw job %s in library %s (%d icons)", job["id"], lib_id, len(names)
    )
    return web.json_response({"job": store.public_job(job), "brief": brief})


async def patch_library(request: web.Request, ctx: Any) -> web.Response:
    """Rename a library, change its parameter set, or repoint its output folder.

    Existing jobs keep the parameters they were drawn with -- their stored params
    are a record of what happened, not a live reference.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    lib_id = request.match_info.get("lib_id", "")
    try:
        body = await _json_body(request)
        fields: dict[str, Any] = {}
        if "name" in body:
            fields["name"] = body["name"]
        if "params" in body:
            fields["params"] = body["params"]
        if "outputPath" in body:
            fields["outputPath"] = body["outputPath"]
        if not fields:
            return web.json_response({"error": "nothing to update"}, status=400)
        lib = await asyncio.to_thread(store.update_library, lib_id, **fields)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"error": f"could not use that folder: {exc}"}, status=400)
    if lib is None:
        return web.json_response({"error": "no such library"}, status=404)
    state = await asyncio.to_thread(store.load_state)
    return web.json_response({"library": store.public_library(state, lib)})


async def reveal_library_folder(request: web.Request, ctx: Any) -> web.Response:
    """Open a library's output folder in the OS file manager.

    Takes NO path. The directory is derived from the library record, so nothing a
    caller sends can point this at another folder -- which matters more here than
    usual, because the app's Python runs in-process with the gateway's privileges
    and this is the one route that hands a path to another program.

    A missing folder is reported, not recreated. The user may have deleted or
    moved it deliberately; conjuring an empty one to show would be a lie about
    where their icons are, and the honest answer points them at Library
    parameters to repoint it.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    lib_id = request.match_info.get("lib_id", "")
    library = await asyncio.to_thread(store.get_library, lib_id)
    if library is None:
        return web.json_response({"error": "no such library"}, status=404)

    path = store.library_output_dir(library)
    if reveal_binary() is None:
        return web.json_response(
            {"error": "opening a folder is not supported on this system"}, status=501
        )
    if not await asyncio.to_thread(path.is_dir):
        return web.json_response(
            {
                "ok": True,
                "opened": False,
                "missing": True,
                "path": str(path),
                "error": "that folder no longer exists",
            },
            status=404,
        )

    try:
        await asyncio.to_thread(reveal_folder, path)
    except RevealError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    logger.info("Icon Studio: revealed %s for library %s", path, lib_id)
    return web.json_response({"ok": True, "opened": True, "path": str(path)})


async def get_library_icons(request: web.Request, ctx: Any) -> web.Response:
    """Every icon in a library, with sanitized inline SVG markup.

    Markup is inlined rather than served as <img> because house icons use
    ``currentColor``, which an <img> cannot resolve -- they would render black on
    a dark dashboard. See ``store.sanitize_svg``.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    lib_id = request.match_info.get("lib_id", "")
    lib = await asyncio.to_thread(store.get_library, lib_id)
    if lib is None:
        return web.json_response({"error": "no such library"}, status=404)
    icons = await asyncio.to_thread(store.library_icons, lib_id)
    return web.json_response({"icons": icons})


async def create_job(request: web.Request, ctx: Any) -> web.Response:
    """Create a job in a library and return the brief for the UI to dispatch."""
    denied = _unauthorized(request)
    if denied:
        return denied
    try:
        body = await _json_body(request)
        fields = store.normalize_job_fields(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    lib_id = str(body.get("libraryId") or "")
    library = await asyncio.to_thread(store.get_library, lib_id) if lib_id else None
    if library is None:
        return web.json_response({"error": "libraryId is required"}, status=400)

    job = await asyncio.to_thread(store.new_job, library, fields)
    brief = store.compose_brief(job, library)
    logger.info(
        "Icon Studio: created job %s in library %s (%d icons)",
        job["id"],
        library["id"],
        len(fields["names"]),
    )
    return web.json_response({"job": store.public_job(job), "brief": brief})


async def render_sheet(request: web.Request, ctx: Any) -> web.Response:
    """Render the contact sheet for a job. Used by the UI's Re-proof button.

    The agent renders its own sheet through ``scripts/contact_sheet.py``; this
    route is the same code path for a user who wants to re-check a finished job
    without starting an agent turn.
    """
    denied = _unauthorized(request)
    if denied:
        return denied
    job_id = request.match_info.get("job_id", "")
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        return web.json_response({"error": "no such job"}, status=404)

    sizes = job.get("params", {}).get("sizes") or None
    try:
        sheet = await asyncio.to_thread(store.render_job_sheet, job_id, sizes)
    except RenderError as exc:
        await asyncio.to_thread(store.update_job, job_id, note=str(exc))
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)

    rel = str(sheet.png_1x).replace(str(workspace_dir()) + "/", "")
    await asyncio.to_thread(store.update_job, job_id, proof=rel, note="")
    return web.json_response(sheet.to_dict())


async def get_proof(request: web.Request, ctx: Any) -> web.Response:
    """Serve a job's contact-sheet PNG so the UI can show it in an <img>."""
    denied = _unauthorized(request)
    if denied:
        return denied
    job_id = request.match_info.get("job_id", "")
    scale = 2 if request.query.get("scale") == "2" else 1
    try:
        path = proof_path(job_id, scale)
    except RenderError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not path.is_file():
        return web.json_response({"error": "no proof yet"}, status=404)
    return web.FileResponse(
        path,
        headers={"Cache-Control": "no-store", "Content-Type": "image/png"},
    )
