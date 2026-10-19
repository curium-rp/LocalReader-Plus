"""Managed reader content, control mailbox and leases. No persistent storage."""

from collections import OrderedDict
import json
import os
import secrets
import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, model_validator
from .falcotype_protocol import StrictModel, Sync, Command, PracticeStatus, BookChunk, TimeCutResult

from ..runtime import runtime


router = APIRouter(prefix="/api/falcotype", tags=["falcotype"])
_token = os.environ.get("LOCALREADER_BRIDGE_TOKEN", "")
_lock = threading.RLock()
_current = None
_page = None
_staged_page = None
_playback = {}
_revision = 0
_updated = 0.0
_session = None
_lease_until = 0.0
_activation = ""
_published_session = None
_transitioning = False
_pending = None
_results = OrderedDict()
_highest_id = 0
_advancement = "command"
_practice = None
_practice_deadline = 0.0
PAGE_LIMIT = 16 * 1024 * 1024


Kind = Literal["text", "heading", "image", "scene"]


class Unit(StrictModel):
    index: int = Field(ge=0)
    kind: Kind


class ChapterScope(StrictModel):
    id: str = Field(min_length=1, max_length=2048)
    start: int = Field(ge=0)
    end: int = Field(ge=1, le=50000)
    title: str = Field(default="", max_length=512)
    index: int | None = Field(default=None, ge=0)


class Page(StrictModel):
    book_id: str = Field(min_length=1, max_length=128)
    page_index: int = Field(ge=0)
    page_revision: int = Field(ge=1)
    html: str = Field(max_length=8 * 1024 * 1024)
    units: list[Unit] = Field(min_length=1, max_length=50000)
    chapters: list[ChapterScope] = Field(default_factory=list, max_length=50000)
    filename: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def ordered_units(self):
        if any(unit.index != index for index, unit in enumerate(self.units)):
            raise ValueError("Page units must use consecutive sentence indexes.")
        end = 0
        for chapter in self.chapters:
            if chapter.start != end or not end < chapter.end <= len(self.units):
                raise ValueError("Chapter scopes must cover consecutive canonical units.")
            end = chapter.end
        if self.chapters and end != len(self.units):
            raise ValueError("Chapter scopes must cover the complete page.")
        return self


class CurrentSentence(StrictModel):
    book_id: str = Field(min_length=1, max_length=128)
    title: str = Field(max_length=512)
    page_index: int = Field(ge=0)
    page_revision: int = Field(ge=1)
    sentence_index: int = Field(ge=0)
    sentence_count: int = Field(ge=1)
    selection_revision: int = Field(ge=0)
    kind: Kind


class Playback(StrictModel):
    state: Literal["idle", "loading", "ready", "playing", "paused", "ended", "error"] = "idle"
    user_paused: bool = False
    audio_enabled: bool = True
    audio_ended: bool = False
    chunk: BookChunk | None = None
    time_cut: TimeCutResult | None = None
    run_action: TimeCutResult | None = None
    practice_complete: bool = False
    book_ended: bool = False
    continuation_from: int | None = Field(default=None, ge=0)
    practice_sequence: int = Field(default=0, ge=0)
    advance_gate: str = Field(default="", max_length=80)
    media_waiting: bool = False
    media_remaining_ms: int = Field(default=0, ge=0)
    error: str = Field(default="", max_length=1024)


class Acknowledgment(StrictModel):
    id: int = Field(ge=1)
    status: Literal["applied", "rejected"]
    error: str = Field(default="", max_length=1024)


class Selection(StrictModel):
    current: CurrentSentence | None
    playback: Playback = Field(default_factory=Playback)
    session_id: str | None = Field(default=None, max_length=128)
    ack: Acknowledgment | None = None
    transitioning: bool = False


def require_managed(request: Request):
    if not runtime.submodule or not _token:
        raise HTTPException(404, "Reader bridge is not enabled.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Reader bridge requires the reader's own origin.")


def require_host(request: Request):
    require_managed(request)
    if not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {_token}"):
        raise HTTPException(403, "Reader bridge token is required.")


def expire():
    global _session, _pending, _practice
    now = time.monotonic()
    if _session and (now >= _lease_until or (_published_session == _session and now - _updated >= 6)):
        _session = None
        _pending = None
        _practice = None


def practice_snapshot():
    if not _session or not _practice:
        return None
    remaining = _practice["remaining_ms"] if (_practice["limit_kind"] == "unlimited" or _practice["run_state"] != "running"
        or _practice["media_waiting"] or _practice["clock_paused"]) else max(0, int((_practice_deadline - time.monotonic()) * 1000))
    return {**_practice, "remaining_ms": remaining}


def live_current():
    if time.monotonic() - _updated >= 6 or not _current or not _page:
        return None
    if any(_current[key] != _page[key] for key in ("book_id", "page_index", "page_revision")):
        return None
    return _current


@router.get("/context")
def get_context(request: Request):
    require_managed(request)
    return {"enabled": True}


class OpenBook(StrictModel):
    path: str = Field(min_length=1, max_length=32768)


@router.post("/open")
def open_book(payload: OpenBook, request: Request):
    require_host(request)
    import webview
    from ..files_api import add_book_to_shelf

    if not webview.windows:
        raise HTTPException(503, "LocalReader window is not ready.")
    window = webview.windows[0]
    if not window.evaluate_js("typeof window.__lrOnNativeEpubDrop === 'function'"):
        raise HTTPException(503, "LocalReader page is not ready.")
    result = add_book_to_shelf(payload.path)
    if result.get("status") not in ("ok", "success", "already_in_shelf") or not result.get("book"):
        raise HTTPException(400, "Could not open EPUB in LocalReader.")
    book_json = json.dumps(result["book"], ensure_ascii=False)
    window.evaluate_js(f"(function() {{ window.__lrOnNativeEpubDrop({book_json}); return true; }})()")
    return {"status": "ok"}


@router.post("/page")
def publish_page(page: Page, request: Request):
    require_managed(request)
    global _staged_page
    payload = page.model_dump()
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > PAGE_LIMIT:
        raise HTTPException(413, "Reader page exceeds the sync size limit.")
    with _lock:
        # Retain one committed page and one candidate. A transfer alone cannot invalidate the live cursor.
        _staged_page = payload
    return {"ok": True}


@router.post("/current")
def publish_current(selection: Selection, request: Request):
    require_managed(request)
    global _current, _playback, _revision, _updated, _published_session, _pending, _page, _staged_page, _transitioning, _session, _practice
    current = selection.current.model_dump() if selection.current else None
    playback = selection.playback.model_dump()
    with _lock:
        expire()
        if current:
            page = next((candidate for candidate in (_staged_page, _page) if candidate and
                         all(current[key] == candidate[key] for key in ("book_id", "page_index", "page_revision"))), None)
            if (not page or current["sentence_count"] != len(page["units"])
                    or current["sentence_index"] >= current["sentence_count"]
                    or current["kind"] != page["units"][current["sentence_index"]]["kind"]):
                raise HTTPException(409, "Current selection does not match the published page.")
            _page = page
            if page is _staged_page:
                _staged_page = None
        elif not selection.transitioning:
            _page = None
            _staged_page = None
        # The reader's independent watchdog may expire before the host sees a failed heartbeat.
        if _session and _published_session == _session and selection.session_id != _session:
            _session = None
            _pending = None
            _practice = None
        if (current, playback, selection.session_id, selection.transitioning) != (_current, _playback, _published_session, _transitioning):
            _revision += 1
        _current, _playback = current, playback
        _published_session = selection.session_id
        _transitioning = selection.transitioning
        _updated = time.monotonic()
        if selection.ack and _pending and selection.session_id == _session and selection.ack.id == _pending["id"]:
            _results[selection.ack.id] = selection.ack.model_dump()
            _pending = None
        return {"sync": {"session_id": _session, "advancement": _advancement,
                         "remaining_ms": max(0, int((_lease_until - time.monotonic()) * 1000)) if _session else 0},
                "practice": practice_snapshot() if selection.session_id == _session else None,
                "command": _pending if selection.session_id == _session else None}


@router.get("/current")
def get_current(request: Request, session_id: str | None = None):
    require_host(request)
    global _lease_until
    with _lock:
        expire()
        if session_id and session_id == _session:
            _lease_until = time.monotonic() + 6
        return {"current": live_current(), "playback": _playback, "revision": _revision,
                "sync": {"session_id": _session, "advancement": _advancement, "applied": bool(_session and _published_session == _session)},
                "ack": next(reversed(_results.values()), None), "transitioning": _transitioning}


@router.post("/sync")
def set_sync(sync: Sync, request: Request):
    require_host(request)
    global _session, _lease_until, _activation, _pending, _highest_id, _advancement, _practice
    with _lock:
        expire()
        if sync.active:
            if not live_current():
                raise HTTPException(503, "No active reader page is ready to sync.")
            if _session and sync.request_id != _activation:
                raise HTTPException(409, "Another sync session is active.")
            if _session and sync.advancement != _advancement:
                raise HTTPException(409, "Advancement mode cannot change during a sync session.")
            if not _session:
                _session = secrets.token_urlsafe(24)
                _activation = sync.request_id
                _pending = None
                _results.clear()
                _highest_id = 0
                _advancement = sync.advancement
                _practice = None
            _lease_until = time.monotonic() + 6
        else:
            if _session and sync.session_id != _session:
                raise HTTPException(409, "Sync session has changed.")
            _session = None
            _pending = None
            _practice = None
        return {"session_id": _session, "advancement": _advancement}


@router.post("/practice-status")
def publish_practice(status: PracticeStatus, request: Request):
    require_host(request)
    global _practice, _practice_deadline
    payload = status.model_dump()
    with _lock:
        expire()
        if status.session_id != _session or _published_session != _session or _advancement != "status":
            raise HTTPException(409, "Practice status requires an active status-driven session.")
        if _practice and status.sequence <= _practice["sequence"]:
            if status.sequence == _practice["sequence"] and payload != _practice:
                raise HTTPException(409, "A status sequence cannot be reused with different data.")
            return {"sequence": status.sequence, "status": "accepted" if payload == _practice else "superseded"}
        if (_practice and status.run_id and status.run_id == _practice["run_id"]
                and _practice["run_state"] == "finished" and
                (status.run_state != "finished" or status.outcome != _practice["outcome"])):
            raise HTTPException(409, "A finished run cannot be reopened or change its outcome.")
        if status.run_start:
            if _practice and status.run_id == _practice["run_id"]:
                if payload["run_start"] != _practice["run_start"]:
                    raise HTTPException(409, "The run-start checkpoint is immutable.")
            else:
                current = live_current()
                if not current or any(getattr(status.run_start, key) != current[key]
                        for key in ("book_id", "page_index", "page_revision", "sentence_index")):
                    raise HTTPException(409, "A new run must start at the current reader selection.")
        # Stops apply to the whole run, including a handoff already loading its next page.
        held = (status.run_state == "running" and status.clock_paused and _practice
                and status.run_id == _practice["run_id"] and status.selection_revision == _practice["selection_revision"]
                and payload["chunk"] == _practice["chunk"])
        if status.run_state in ("running", "preparing") and not held:
            current = live_current()
            if not current or status.selection_revision != current["selection_revision"]:
                raise HTTPException(409, "Selected sentence has changed.")
            if status.media_waiting and current["kind"] not in ("image", "scene"):
                raise HTTPException(409, "A media wait requires an image or scene break.")
            if status.chunk:
                chunk = status.chunk
                if (any(getattr(chunk, key) != current[key] for key in ("book_id", "page_index", "page_revision"))
                        or not chunk.start <= current["sentence_index"] < chunk.end <= len(_page["units"])):
                    raise HTTPException(409, "Book chunk belongs to a different canonical selection.")
                if _page["chapters"] and not any(scope["id"] == chunk.chapter_id and
                        scope["start"] <= chunk.start < chunk.end <= scope["end"] for scope in _page["chapters"]):
                    raise HTTPException(409, "Book chunk crosses its reader chapter scope.")
                kinds = [unit["kind"] for unit in _page["units"][chunk.start:chunk.end]]
                if ((chunk.kind in ("image", "scene") and kinds != [chunk.kind]) or
                        ((chunk.kind == "empty" or chunk.kind == "text" and chunk.preset != "chapter") and any(kind in ("image", "scene") for kind in kinds))):
                    raise HTTPException(409, "Book chunk does not match reader media units.")
        _practice = payload
        _practice_deadline = None if status.remaining_ms is None or status.clock_paused else time.monotonic() + status.remaining_ms / 1000
        return {"sequence": status.sequence, "status": "accepted"}


@router.post("/command")
def send_command(command: Command, request: Request, response: Response):
    require_host(request)
    global _pending, _highest_id
    payload = command.model_dump()
    with _lock:
        expire()
        if command.session_id != _session or _published_session != _session:
            raise HTTPException(409, "Sync session is not active in the reader.")
        if command.id in _results:
            if _results[command.id]["status"] == "queued":
                response.status_code = 202
            return _results[command.id]
        if _advancement == "status" and command.type in ("complete", "advance", "continue", "skip_image"):
            raise HTTPException(409, "Automatic advancement is owned by reader practice status; use media_next for media.")
        if not live_current():
            raise HTTPException(409, "Sync session is not active in the reader.")
        if command.id <= _highest_id:
            raise HTTPException(409, "Command is stale; it will not be replayed.")
        if command.selection_revision != _current["selection_revision"]:
            raise HTTPException(409, "Selected sentence has changed.")
        if command.type == "time_cut":
            applied = (_playback.get("time_cut") or {}).get("operation_id") == command.time_cut.operation_id
            if not applied and (_advancement != "status" or not _practice
                    or _practice["run_state"] != "finished" or _practice["remaining_ms"] != 0
                    or _practice["limit_kind"] != "finite"
                    or (_practice["run_id"] is not None and _practice["outcome"] != "time_limit")
                    or _practice.get("chunk") != command.time_cut.chunk.model_dump()
                    or (_practice["selection_revision"] != command.selection_revision
                        and _playback.get("continuation_from") != _practice["selection_revision"])):
                raise HTTPException(409, "Time cut requires the captured expired practice chunk.")
        if command.type == "run_action":
            applied = (_playback.get("run_action") or {}).get("operation_id") == command.run_action.operation_id
            if not applied and (_advancement != "status" or not _practice or _practice["run_state"] != "finished"
                    or _practice["run_id"] != command.run_action.run_id
                    or (_practice["outcome"] not in ("manual_stop", "book_end", "abandoned")
                        and not (_practice["outcome"] == "time_limit" and _playback.get("book_ended") and command.run_action.action == "restart"))
                    or _practice["chunk"] != command.run_action.chunk.model_dump()):
                raise HTTPException(409, "A result action requires its acknowledged finished run.")
        if _advancement == "status" and command.type in ("prepare", "play", "resume"):
            if (not _practice or _practice["selection_revision"] != command.selection_revision
                    or _practice["run_state"] not in ("preparing", "running")
                    or (_practice["run_state"] == "running" and (_practice["clock_paused"] or
                        (_practice_deadline is not None and not _practice["media_waiting"] and time.monotonic() >= _practice_deadline)))):
                raise HTTPException(409, "Practice must be ready before starting audio.")
        if _pending:
            raise HTTPException(409, "A reader command is still pending.")
        _pending = payload
        _highest_id = command.id
        _results[command.id] = {"id": command.id, "status": "queued", "error": ""}
        while len(_results) > 128:
            _results.popitem(last=False)
        response.status_code = 202
        return _results[command.id]


@router.get("/page")
def get_page(book_id: str, page_index: int, page_revision: int, request: Request):
    require_host(request)
    with _lock:
        current = live_current()
        if not current or (book_id, page_index, page_revision) != tuple(current[key] for key in ("book_id", "page_index", "page_revision")):
            raise HTTPException(409, "Reader page has changed.")
        return _page


@router.get("/image/{book_id}/{image_id}")
def get_image(book_id: str, image_id: str, request: Request):
    require_host(request)
    with _lock:
        current = live_current()
        if not current or book_id != current["book_id"]:
            raise HTTPException(404, "No active book image.")
    from ..routers.redirect import get_library_image
    return get_library_image(book_id, image_id)
