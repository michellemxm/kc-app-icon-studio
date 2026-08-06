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

from . import store
from .contact_sheet import RenderError, find_browser, proof_path, render_job, workspace_dir

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
    denied = _unauthorized(request)
    if denied:
        return denied
    try:
        body = await _json_body(request)
        params = store.normalize_library_params(body.get("params") or body)
        lib = await asyncio.to_thread(store.new_library, body.get("name"), params)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    logger.info("Icon Studio: created library %s (%s)", lib["id"], lib["name"])
    state = await asyncio.to_thread(store.load_state)
    return web.json_response({"library": store.public_library(state, lib)})


async def patch_library(request: web.Request, ctx: Any) -> web.Response:
    """Rename a library or change its parameter set.

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
        if not fields:
            return web.json_response({"error": "nothing to update"}, status=400)
        lib = await asyncio.to_thread(store.update_library, lib_id, **fields)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if lib is None:
        return web.json_response({"error": "no such library"}, status=404)
    state = await asyncio.to_thread(store.load_state)
    return web.json_response({"library": store.public_library(state, lib)})


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
        sheet = await asyncio.to_thread(render_job, job_id, sizes)
    except RenderError as exc:
        await asyncio.to_thread(store.update_job, job_id, note=str(exc))
        return web.json_response({"error": str(exc)}, status=409)

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
