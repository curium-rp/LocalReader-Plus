"""
Deep Adapter & In-Memory Chapter/Image Streaming Router (redirect.py)
--------------------------------------------------------------------
Non-destructive seam that bridges the native C++ peeker and in-memory
hybrid pipeline with the FastAPI web server and reader frontend.

Exposes:
1. Native Streaming Endpoints:
   - GET /api/books/{doc_id}/meta
   - GET /api/books/{doc_id}/chapter/{index}
   - GET /api/books/{doc_id}/image/{image_id}
   - GET /api/books/{doc_id}/search?q=...
   - GET /api/books/{doc_id}/footnote?file=...&anchor=...
2. Backward-Compatible Interceptors:
   - GET /api/library/content/{doc_id}  (in-memory synthesis for peeked books)
   - GET /api/library/image/{doc_id}/{image_id}  (zero-disk streaming from .epub)
3. Reading Progress:
   - POST /api/library/progress/{doc_id}  (peek + PDF: progress.json; ?flush=true rotates .old)
   - POST /api/library/progress/flush
   - POST /api/books/{doc_id}/shelf-status  (peek KOReader shelf only; metadata/PDF has no status)
   - GET /api/library  (peek: userdata/library/<id>/; PDF/legacy: userdata/metadata/<id>/)
   - POST /api/library  (PDF/legacy -> metadata/<id>/info.json + progress.json)
   - DELETE /api/library/{doc_id}
"""

import os
import json
import time
import mimetypes
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Response, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import content_dir
from ..models import LibraryItem
from ..routers.library import ProgressUpdatePayload, escape_search_query, html_to_search_text
from ..logic.memories import (
    call_peeker_manifest,
    call_peeker_single,
    call_peeker_stream_html,
    intercept_manifest,
    intercept_chapter,
    synthesize_full_content,
    memory_manager,
    meta_maps_need_rebuild,
    resolve_image_map_entry,
    load_book_info,
    get_peek_source_path,
    is_peek_book,
    is_pdf_sidecar_book,
    save_book_progress,
    load_any_progress,
    list_peek_catalog,
    list_pdf_catalog,
    set_book_shelf_status,
    upsert_pdf_sidecar,
    delete_library_book,
    source_epub_is_present,
)

MISSING_BOOK_DETAIL = "Book missing from path"

router = APIRouter(tags=["redirect"])


def guess_image_mime(filename_or_path: str) -> str:
    """Determine MIME type for images, defaulting to image/jpeg."""
    clean = str(filename_or_path or "").split("?")[0].split("#")[0].lower()
    if clean.endswith(".png"):
        return "image/png"
    elif clean.endswith(".webp"):
        return "image/webp"
    elif clean.endswith(".gif"):
        return "image/gif"
    elif clean.endswith(".svg"):
        return "image/svg+xml"
    elif clean.endswith(".avif"):
        return "image/avif"
    mime, _ = mimetypes.guess_type(clean)
    return mime or "image/jpeg"


def require_source_epub(meta: Optional[Dict[str, Any]] = None, epub_path: Optional[str] = None) -> str:
    """Raise 404 when the peek EPUB was moved, renamed, or deleted."""
    path = epub_path or ((meta.get("source_path") or meta.get("path")) if meta else None)
    if not source_epub_is_present(path):
        raise HTTPException(status_code=404, detail=MISSING_BOOK_DETAIL)
    return str(path)


def get_book_source_path(doc_id: str) -> Optional[str]:
    """
    External EPUB path for peek books, or None for legacy unzip/PDF.
    Peek path lives in userdata/library/<id>/info.json (catalog rows are id+name only).
    """
    peek_path = get_peek_source_path(doc_id)
    if peek_path:
        return peek_path
    return None


def get_or_create_meta(doc_id: str) -> Optional[Dict[str, Any]]:
    """Load userdata/library/<id>/info.json, or build it from the EPUB on first open."""
    book_path = get_book_source_path(doc_id)
    meta = load_book_info(doc_id)

    if meta:
        meta_path = meta.get("source_path") or meta.get("path") or book_path
        if not meta_path:
            return None
        if meta_maps_need_rebuild(meta):
            src = Path(meta_path)
            if src.exists() and src.is_file():
                cpp_manifest = call_peeker_manifest(src)
                meta = intercept_manifest(cpp_manifest, src, doc_id=doc_id)
        if not memory_manager.has_session(doc_id):
            memory_manager.register_session(doc_id, meta)
        return meta

    if book_path:
        try:
            src = Path(book_path)
            if src.exists() and src.is_file():
                cpp_manifest = call_peeker_manifest(src)
                return intercept_manifest(cpp_manifest, src, doc_id=doc_id)
        except Exception as e:
            print(f"[Redirect] Auto-manifest generation failed for {doc_id}: {e}")

    return None


def stream_html_caller(epub_path: Path | str, **kwargs) -> Dict[str, bytes]:
    """Helper bridging call_peeker_stream_html with kwargs flexibility."""
    return call_peeker_stream_html(epub_path)


# ---------------------------------------------------------------------------
# 1. Native Streaming Endpoints (/api/books/...)
# ---------------------------------------------------------------------------

@router.get("/api/books/{doc_id}/meta")
def get_book_metadata(doc_id: str):
    """Returns lightweight book metadata (< 40KB meta.json)."""
    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")
    return meta


@router.get("/api/books/{doc_id}/chapter/{index}")
def get_book_chapter(doc_id: str, index: int):
    """
    Tier 1 Instant Chapter Peek (< 0.2ms).
    Returns cleaned HTML with scoped sentence/paragraph IDs for a single chapter,
    along with the list of contained images preloaded in RAM.
    """
    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")

    spine = meta.get("spine", [])
    if index < 0 or index >= len(spine):
        raise HTTPException(status_code=400, detail=f"Chapter index {index} out of bounds (0..{len(spine)-1})")

    session = memory_manager.get_session(doc_id)
    cached_html = session["chapters"].get(index) if session else None

    if cached_html is None:
        epub_path = meta.get("source_path")
        chapter_href = spine[index]
        try:
            raw_bytes = memory_manager.get_raw_chapter_bytes(doc_id, meta, index)
            if raw_bytes is None:
                require_source_epub(meta, epub_path)
                raw_bytes = call_peeker_single(epub_path, chapter_href)
        except HTTPException:
            raise
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=MISSING_BOOK_DETAIL)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to decompress chapter {chapter_href}: {e}")

        html_content = intercept_chapter(
            raw_bytes,
            chapter_href,
            meta,
            index,
            doc_id=doc_id,
        )
    else:
        html_content = cached_html

    memory_manager.start_background_text_buffer(doc_id, meta, stream_html_caller)

    # Chapter images list from metadata
    images = meta.get("chapter_images", {}).get(str(index), [])

    return {
        "chapter_index": index,
        "total_chapters": len(spine),
        "chapter_href": spine[index],
        "html": html_content,
        "images": images,
    }


@router.get("/api/books/{doc_id}/buffer")
def get_book_buffer_status(doc_id: str):
    """Tier 2 RAM buffer status. Frontend waits for ready before EPUB page math."""
    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")
    payload = memory_manager.get_buffer_status(doc_id)
    if payload.get("total") == 0:
        payload["total"] = len(meta.get("spine") or [])
        payload["ready"] = payload["total"] > 0 and payload.get("buffered", 0) >= payload["total"]
    return payload


@router.get("/api/books/{doc_id}/image/{image_id}")
def get_book_image(doc_id: str, image_id: str):
    """
    Tier 3 On-Demand Image Peek directly from .epub container.
    Cached in RAM for instant subsequent access. Zero disk writes.
    Served with aggressive HTTP caching.
    """
    clean_id = urllib.parse.unquote(image_id).strip()

    # Fast path: check in-memory RAM cache (< 0.05ms)
    cached_bytes = memory_manager.get_cached_image(doc_id, clean_id)
    if cached_bytes:
        mime_type = guess_image_mime(clean_id)
        return Response(
            content=cached_bytes,
            media_type=mime_type,
            headers={
                "Cache-Control": "public, max-age=86400, immutable",
                "Content-Length": str(len(cached_bytes)),
                "X-Cache": "RAM",
            },
        )

    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")

    epub_path = require_source_epub(meta)

    image_map = meta.get("image_map", {})
    matched = resolve_image_map_entry(image_map, clean_id)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found in book manifest")
    matched_id, inner_path = matched

    try:
        raw_bytes = call_peeker_single(epub_path, inner_path)
        memory_manager.cache_image(doc_id, matched_id, raw_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract image bytes: {e}")

    mime_type = guess_image_mime(inner_path)
    return Response(
        content=raw_bytes,
        media_type=mime_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Length": str(len(raw_bytes)),
            "X-Cache": "MISS",
        },
    )


@router.get("/api/books/{doc_id}/search")
def search_in_book(
    doc_id: str,
    q: str = Query(..., min_length=1),
    match_case: bool = False,
    whole_word: bool = False,
):
    """In-memory search across the Tier 2 RAM buffer, honoring search.js flags."""
    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")

    matches = memory_manager.search_in_memory(doc_id, q, match_case=match_case, whole_word=whole_word)
    return {
        "results": matches,
        "total_matches": len(matches),
        "query": q,
    }


@router.get("/api/books/{doc_id}/footnote")
def resolve_footnote(doc_id: str, file: str = Query(...), anchor: str = Query(...)):
    """Resolves cross-chapter footnote definitions on-demand from Tier 2 RAM buffer."""
    meta = get_or_create_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Book metadata not found")

    footnote_html = memory_manager.resolve_footnote(doc_id, file, anchor)
    if not footnote_html:
        raise HTTPException(status_code=404, detail="Footnote definition not found")

    return {"html": footnote_html, "file": file, "anchor": anchor}


# ---------------------------------------------------------------------------
# 2. Transparent Deep Adapter for Legacy Frontend (/api/library/...)
# ---------------------------------------------------------------------------

@router.get("/api/library/content/{doc_id}")
def get_library_content(doc_id: str):
    """
    Transparent Adapter for frontend library.js / selectDocument():
    - If doc_id has path (new code): synthesizes full content in RAM in ~19ms with zero disk extraction.
    - If doc_id has no path (legacy code): directly falls back to disk content JSON.
    """
    book_path = get_book_source_path(doc_id)
    if book_path:
        require_source_epub(epub_path=book_path)
        meta = get_or_create_meta(doc_id)
        if meta and (meta.get("source_path") or meta.get("path")):
            try:
                return synthesize_full_content(doc_id, meta, stream_html_caller)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=MISSING_BOOK_DETAIL)

    # Legacy fallback: check userdata/content/<doc_id>/<doc_id>.json
    book_dir = content_dir / doc_id
    legacy_json = book_dir / f"{doc_id}.json"
    if not legacy_json.exists():
        legacy_json = content_dir / f"{doc_id}.json"

    if legacy_json.exists():
        try:
            with open(legacy_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed reading legacy content: {e}")

    raise HTTPException(status_code=404, detail="Book content not found")


@router.get("/api/library/image/{doc_id}/{image_id}")
def get_library_image(doc_id: str, image_id: str):
    """
    Transparent Adapter for image rendering:
    - If doc_id has path (new code): peeks image directly from .epub into RAM (< 0.5ms).
    - If doc_id has no path (legacy code): falls back to disk image in userdata/content/<doc_id>/.
    """
    book_path = get_book_source_path(doc_id)
    if book_path:
        meta = get_or_create_meta(doc_id)
        if meta and (meta.get("source_path") or meta.get("path")):
            return get_book_image(doc_id, image_id)

    # Legacy fallback: read from content directory
    clean_id = urllib.parse.unquote(image_id).strip()
    book_dir = content_dir / doc_id
    if book_dir.exists():
        # Check if legacy content JSON has an image map
        legacy_json = book_dir / f"{doc_id}.json"
        mapped_name = clean_id
        if legacy_json.exists():
            try:
                with open(legacy_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mapped_name = data.get("image_map", {}).get(clean_id, clean_id)
            except Exception:
                pass

        image_path = book_dir / mapped_name
        if image_path.exists() and image_path.is_file():
            return FileResponse(image_path)

    raise HTTPException(status_code=404, detail="Image not found")


@router.get("/api/library/search/{doc_id}")
def search_library_book(doc_id: str, q: str = Query(..., min_length=1), match_case: bool = False, whole_word: bool = False):
    """
    Search endpoint compatible with search.js:
    - If book has path (new code): searches RAM buffer chapters.
    - If book has no path (legacy code): reads userdata/content/<doc_id>/<doc_id>.json.
    """
    book_path = get_book_source_path(doc_id)
    if book_path:
        require_source_epub(epub_path=book_path)
        meta = get_or_create_meta(doc_id)
        if meta and (meta.get("source_path") or meta.get("path")):
            session = memory_manager.get_session(doc_id)
            spine_len = len(meta.get("spine") or [])
            buffered = 0
            status = None
            if session:
                with memory_manager._lock:
                    buffered = len(session.get("chapters") or {})
                    status = session.get("status")
            if not session or status != "complete" or (spine_len and buffered < spine_len):
                try:
                    synthesize_full_content(doc_id, meta, stream_html_caller)
                except FileNotFoundError:
                    raise HTTPException(status_code=404, detail=MISSING_BOOK_DETAIL)
            raw_matches = memory_manager.search_in_memory(
                doc_id, q, match_case=match_case, whole_word=whole_word
            )
            page_map: Dict[int, List[Dict[str, Any]]] = {}
            for m in raw_matches:
                p_idx = m["page_index"]
                if p_idx not in page_map:
                    page_map[p_idx] = []
                page_map[p_idx].append({"position": m["position"], "snippet": m["snippet"]})
            results = [
                {"page_index": p_idx, "match_count": len(m_list), "matches": m_list[:3]}
                for p_idx, m_list in sorted(page_map.items())
            ]
            return {
                "results": results,
                "total_matches": len(raw_matches),
                "query": q,
                "pages_with_matches": len(results),
            }

    # Legacy code search: read from disk JSON
    book_dir = content_dir / doc_id
    legacy_json = book_dir / f"{doc_id}.json"
    if not legacy_json.exists():
        legacy_json = content_dir / f"{doc_id}.json"

    if legacy_json.exists():
        try:
            with open(legacy_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            pages = data.get("pages", [])
            import re
            flags = 0 if match_case else re.IGNORECASE
            q_norm = q.replace('‘', "'").replace('’', "'").replace('´', "'").replace('`', "'").replace('“', '"').replace('”', '"')
            escaped_q = escape_search_query(q_norm)
            if not escaped_q:
                return {"results": [], "total_matches": 0, "query": q, "pages_with_matches": 0}
            pattern_str = rf"\b{escaped_q}\b" if whole_word else escaped_q
            try:
                pattern = re.compile(pattern_str, flags)
            except Exception:
                return {"results": [], "total_matches": 0, "query": q, "pages_with_matches": 0}
            results = []
            total_matches = 0
            for page_index, page_html in enumerate(pages):
                page_text = html_to_search_text(page_html)
                matches_list = []
                for match in pattern.finditer(page_text):
                    pos = match.start()
                    context_start = max(0, pos - 50)
                    context_end = min(len(page_text), match.end() + 50)
                    snippet = page_text[context_start:context_end].strip()
                    if context_start > 0: snippet = "..." + snippet
                    if context_end < len(page_text): snippet = snippet + "..."
                    matches_list.append({"position": pos, "snippet": snippet})
                if matches_list:
                    results.append({"page_index": page_index, "match_count": len(matches_list), "matches": matches_list[:3]})
                    total_matches += len(matches_list)
            return {"results": results, "total_matches": total_matches, "query": q, "pages_with_matches": len(results)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Legacy search failed: {e}")

    raise HTTPException(status_code=404, detail="Book not found for search")


# ---------------------------------------------------------------------------
# 3. Reading Progress (progress.json + progress.json.old)
# ---------------------------------------------------------------------------

def flush_progress_to_library(target_doc_id: Optional[str] = None) -> bool:
    """Rotate progress.json -> progress.json.old, then rewrite (same as peek flush)."""
    if not target_doc_id:
        return True
    peek = is_peek_book(target_doc_id)
    pdf = (not peek) and is_pdf_sidecar_book(target_doc_id)
    if not peek and not pdf:
        return False
    entry = load_any_progress(target_doc_id)
    if not entry:
        return True
    try:
        save_book_progress(
            target_doc_id,
            entry,
            rotate_old=True,
            kind="pdf" if pdf else "peek",
        )
        return True
    except Exception as e:
        print(f"[Progress] Flush to progress.json failed: {e}")
        return False


@router.post("/api/library/progress/flush")
def flush_progress_endpoint(doc_id: Optional[str] = None):
    """Rotate that book's progress.json to progress.json.old, then rewrite."""
    success = flush_progress_to_library(target_doc_id=doc_id)
    return {"status": "success" if success else "error", "flushed_doc": doc_id}


@router.post("/api/library/progress/{doc_id}")
def update_book_progress_intercept(doc_id: str, payload: ProgressUpdatePayload, flush: bool = False):
    """
    Peek: userdata/library/<id>/progress.json (+ progress.json.old on flush).
    PDF/legacy: userdata/metadata/<id>/progress.json (same rotate; no shelf_status).
    """
    entry = {
        "currentPage": payload.currentPage,
        "lastSentenceId": payload.lastSentenceId,
        "lastSentenceIndex": payload.lastSentenceIndex,
        "lastAccessed": payload.lastAccessed,
        "current_page": payload.current_page,
        "total_pages": payload.total_pages,
        "progress_percent": payload.progress_percent,
        "updated_at": time.time(),
    }

    peek = is_peek_book(doc_id)
    pdf = (not peek) and is_pdf_sidecar_book(doc_id)
    if not peek and not pdf:
        raise HTTPException(status_code=404, detail="Book not found")
    try:
        prev = load_any_progress(doc_id) or {}
        if payload.disable_br is None:
            entry["disable_br"] = prev.get("disable_br", False)
        else:
            entry["disable_br"] = bool(payload.disable_br)
        save_book_progress(
            doc_id,
            entry,
            rotate_old=bool(flush),
            kind="pdf" if pdf else "peek",
        )
    except Exception as e:
        print(f"[Progress] Sidecar progress write failed for {doc_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save progress")
    return {
        "status": "success",
        "message": f"Checkpoint {'flushed' if flush else 'saved'} to progress.json for {doc_id}",
    }


class ShelfStatusPayload(BaseModel):
    status: str


@router.post("/api/books/{doc_id}/shelf-status")
def set_book_shelf_status_endpoint(doc_id: str, payload: ShelfStatusPayload):
    """Peek-only KOReader shelf: reading clears the key; hold/finished are stored in progress.json."""
    raw = str(payload.status or "").strip().lower().replace("_", " ")
    if raw in ("on hold",):
        raw = "hold"
    if raw not in ("reading", "hold", "finished"):
        raise HTTPException(status_code=400, detail="status must be reading, hold, or finished")
    if not is_peek_book(doc_id):
        raise HTTPException(status_code=404, detail="Peek book not found")
    try:
        item = set_book_shelf_status(doc_id, raw)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Peek book not found")
    except Exception as e:
        print(f"[Shelf] Failed to set status for {doc_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save shelf status")
    return {
        "status": "ok",
        "book": item,
        "shelf_status": item.get("shelf_status") or "reading",
    }


@router.get("/api/library")
def get_library_intercept():
    """Library tab: peek from userdata/library/<id>/, PDF/legacy from userdata/metadata/<id>/."""
    return list_peek_catalog() + list_pdf_catalog()


@router.post("/api/library")
async def save_library_item_intercept(item: LibraryItem):
    """PDF/legacy catalog writes userdata/metadata/<id>/info.json + progress.json. Peek is a no-op."""
    if is_peek_book(item.id):
        return {"status": "ok"}
    payload = item.model_dump()
    try:
        upsert_pdf_sidecar(payload)
    except Exception as e:
        print(f"[Library] PDF sidecar save failed for {item.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save library item")
    return {"status": "ok"}


@router.delete("/api/library/{doc_id}")
async def delete_library_item_intercept(doc_id: str):
    if not delete_library_book(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted"}

