from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
import json, re, urllib.parse, shutil, html, sys, asyncio
from pathlib import Path
from ..config import content_dir
from ..models import LibraryItem
from ..utils import (
    safe_save_json,
    language_from_pages,
    language_from_pdf_doc,
    language_from_text_heuristic,
)

base_dir = Path(__file__).parent.parent
if str(base_dir) not in sys.path:
    sys.path.append(str(base_dir))

try:
    import logic.html_normalizer as _html_norm
    import logic.epub_normalizer as _epub_norm
except ImportError:
    from ..logic import html_normalizer as _html_norm
    from ..logic import epub_normalizer as _epub_norm

router = APIRouter()
_library_lock = asyncio.Lock()

class ProgressUpdatePayload(BaseModel):
    currentPage: int
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: int
    lastAccessed: float
    current_page: Optional[int] = None
    total_pages: Optional[int] = None
    progress_percent: Optional[int] = None
    disable_br: Optional[bool] = None

# Locate JSON metadata file for a given document ID
def get_doc_json_path(doc_id: str) -> Path:
    p = content_dir / doc_id / f"{doc_id}.json"
    if p.exists(): return p
    p = content_dir / f"{doc_id}.json"
    if p.exists(): return p
    raise HTTPException(404, "Document not found")


# Restore PDF inline markers back to real HTML tags
def _restore_pdf_inline_markers(text: str) -> str:
    """Turn PDF convert markers into HTML. Sentence split is done in the reader."""
    clean = text.replace("<ABBR>", ".")
    clean = (
        clean.replace("@@B_ON@@", "<b>").replace("@@B_OFF@@", "</b>")
        .replace("@@I_ON@@", "<i>").replace("@@I_OFF@@", "</i>")
        .replace("@@U_ON@@", "<u>").replace("@@U_OFF@@", "</u>")
        .replace("@@D_ON@@", "<del>").replace("@@D_OFF@@", "</del>")
        .replace("@@F_ON@@ ", "").replace("@@F_ON@@", "")
        .replace(" @@F_OFF_A@@", "</a>").replace("@@F_OFF_A@@", "</a>")
        .replace(" @@F_OFF_SUP@@", "</sup></a>")
        .replace("@@F_OFF_SUP@@", "</sup></a>")
        .replace(" @@BR@@ ", "<br/>").replace("@@BR@@", "<br/>")
    )
    clean = re.sub(
        r"@@F_S\|([^@|]*)\|SUP@@\s*",
        r'<a epub:type="noteref" href="#\1"><sup>',
        clean,
    )
    clean = re.sub(
        r"@@F_S\|([^@|]*)\|A@@\s*",
        r'<a epub:type="noteref" href="#\1">',
        clean,
    )
    clean = re.sub(
        r"@@F_E\|([^@|]*)@@\s*",
        r'<a epub:type="footnote" id="\1">',
        clean,
    )
    # 🌟 RESTORE EXTERNAL LINKS (mirrors restore_inline_markers)
    def _restore_ext_link(match):
        raw_url = urllib.parse.unquote(match.group(1))
        inner_text = match.group(2)
        safe_url = html.escape(raw_url, quote=True)
        return f'<a href="{safe_url}" class="external-link" target="_blank" rel="noopener noreferrer">{inner_text}</a>'

    return re.sub(
        r"@@EXT_A\|([^@|]+)@@(.*?)@@EXT_A_OFF@@",
        _restore_ext_link,
        clean,
        flags=re.DOTALL,
    )


# Wrap one PDF block with a TTS id
def master_sentence_splitter(
    text: str, start_idx: int = 0, block_tag: str = "p"
):
    """Wrap one PDF block with a TTS id. Reader JS injects <n> sentences."""
    text = (text or "").strip()
    if not text:
        return "", start_idx
    if block_tag not in (
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td"
    ):
        block_tag = "p"

    clean = _restore_pdf_inline_markers(re.sub(r"\.\s+\.\s+\.", "...", text))
    visible = re.sub(r"<[^>]+>", "", clean)
    visible = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", visible)
    if not visible:
        return "", start_idx
    return (
        f'<{block_tag} id="s_{start_idx}">{clean.strip()}</{block_tag}>',
        start_idx + 1,
    )


# Convert PyMuPDF Rect to plain (x0,y0,x1,y1) tuple
def _pdf_rect_tuple(rect) -> Optional[tuple]:
    if rect is None:
        return None
    if hasattr(rect, "x0"):
        return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
    try:
        return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except (TypeError, ValueError, IndexError):
        return None


# Convert PyMuPDF Point to plain (x,y) tuple
def _pdf_point_xy(point) -> Optional[tuple]:
    if point is None or isinstance(point, str):
        return None
    if hasattr(point, "x"):
        return (float(point.x), float(point.y))
    try:
        return (float(point[0]), float(point[1]))
    except (TypeError, ValueError, IndexError):
        return None


# True if two bounding rectangles overlap within pad tolerance
def _pdf_rects_intersect(span_bbox, link_rect, pad: float = 0.5) -> bool:
    if not span_bbox or not link_rect:
        return False
    ax0, ay0, ax1, ay1 = span_bbox
    bx0, by0, bx1, by1 = link_rect
    return not (
        ax1 < bx0 - pad
        or ax0 > bx1 + pad
        or ay1 < by0 - pad
        or ay0 > by1 + pad
    )


# Compute overlap area between two bounding boxes
def _pdf_overlap_area(a, b) -> float:
    if not a or not b:
        return 0.0
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


# Compute area of a bounding box
def _pdf_bbox_area(bbox) -> float:
    if not bbox:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


# True if point lies within bbox with padding
def _pdf_point_in_bbox(point_xy, bbox, pad: float = 2.0) -> bool:
    if not point_xy or not bbox:
        return False
    x, y = point_xy
    x0, y0, x1, y1 = bbox
    return (x0 - pad) <= x <= (x1 + pad) and (y0 - pad) <= y <= (y1 + pad)


_PDF_CALLOUT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,3}|[ivxlcdm]{1,4}|[*†‡§¶※]+)(?![A-Za-z0-9])",
    re.I,
)
_PDF_FOOTNOTE_START_RE = re.compile(
    r"^\s*(?:\d{1,3}|[ivxlcdm]{1,4}|[*†‡§¶※])[.)]?\s+\S",
    re.I,
)


# True if a PDF text span looks like a footnote callout (superscript number/symbol)
def _pdf_is_callout_token(text: str, flags: int = 0) -> bool:
    token = (text or "").strip()
    if not token:
        return False
    if len(token) <= 4 and _PDF_CALLOUT_TOKEN_RE.fullmatch(token):
        return True
    if (flags & 1) and len(token) <= 6 and re.fullmatch(r"[\w*†‡§¶※]+", token):
        return True
    return False


# Strip HTML markers from PDF text for display/matching
def _pdf_visible_plain(markup: str) -> str:
    text = re.sub(r"@@F_S\|[^@|]*\|SUP@@", "", markup or "")
    text = text.replace("@@F_OFF_SUP@@", "")
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


# Scan PDF link annotations to collect goto-note targets
def _pdf_collect_goto_notes(doc) -> List[Dict[str, Any]]:
    """Pair internal GOTO hotspots with destinations for PDF footnote wrapping."""
    import pymupdf

    notes: List[Dict[str, Any]] = []
    seen_ids = set()
    for pno in range(len(doc)):
        try:
            links = doc[pno].get_links()
        except Exception:
            continue
        for link in links:
            if link.get("kind") != pymupdf.LINK_GOTO:
                continue
            dest_page = link.get("page")
            if dest_page is None or dest_page < 0 or dest_page >= len(doc):
                continue
            src_rect = _pdf_rect_tuple(link.get("from"))
            if not src_rect:
                continue
            width = src_rect[2] - src_rect[0]
            height = src_rect[3] - src_rect[1]
            # TOC/chapter GOTOs are wide. Footnote markers stay narrow even when
            # the hotspot is as tall as the body line box.
            if width > 48 or (width > 16 and height > 22):
                continue
            dest_point = _pdf_point_xy(link.get("to"))
            if dest_page < pno:
                continue
            if (
                dest_page == pno
                and dest_point is not None
                and dest_point[1] <= src_rect[3]
            ):
                continue
            if dest_point is not None:
                note_id = f"R_{dest_page}_{int(dest_point[0])}_{int(dest_point[1])}"
            else:
                note_id = f"R_{pno}_{int(src_rect[0])}_{int(src_rect[1])}"
            if note_id in seen_ids:
                for existing in notes:
                    if existing["id"] == note_id:
                        existing.setdefault("src_rects", []).append((pno, src_rect))
                        break
                continue
            seen_ids.add(note_id)
            notes.append({
                "id": note_id,
                "src_page": pno,
                "src_rect": src_rect,
                "src_rects": [(pno, src_rect)],
                "dst_page": dest_page,
                "dst_point": dest_point,
            })
    return notes


# Score how likely a span is the source callout for a note
def _pdf_note_src_score(span_bbox, text: str, flags: int, note: Dict[str, Any], page_index: Optional[int]):
    best = None
    for src_page, src_rect in note.get("src_rects") or [(note["src_page"], note["src_rect"])]:
        if page_index is not None and src_page != page_index:
            continue
        overlap = _pdf_overlap_area(span_bbox, src_rect)
        if overlap <= 0 and not _pdf_rects_intersect(span_bbox, src_rect, pad=0.25):
            continue
        span_area = _pdf_bbox_area(span_bbox) or 1.0
        center = (
            (span_bbox[0] + span_bbox[2]) / 2.0,
            (span_bbox[1] + span_bbox[3]) / 2.0,
        )
        center_in = _pdf_point_in_bbox(center, src_rect, pad=0.25)
        callout = _pdf_is_callout_token(text, flags)
        overlap_ratio = overlap / span_area
        has_embedded = bool(_PDF_CALLOUT_TOKEN_RE.search(text or ""))
        if not callout and not center_in and overlap_ratio < 0.35 and not (has_embedded and overlap > 0):
            continue
        if not callout and len((text or "").strip()) > 8 and overlap_ratio < 0.55:
            if not (has_embedded and overlap > 0):
                continue
        score = overlap + (80.0 if callout else 0.0) + (12.0 if center_in else 0.0)
        if has_embedded and not callout:
            score += 18.0
        score -= min(len((text or "").strip()), 80) * 0.6
        if best is None or score > best:
            best = score
    return best


# Wrap a footnote callout span in marker tags
def _pdf_wrap_callout_markup(cleaned: str, note_id: str, span_bbox, src_rect, flags: int) -> str:
    stripped = cleaned.strip()
    if _pdf_is_callout_token(stripped, flags):
        return f'@@F_S|{note_id}|SUP@@{html.escape(stripped)}@@F_OFF_SUP@@ '
    match = None
    if span_bbox and src_rect and (span_bbox[2] > span_bbox[0]):
        ox0 = max(span_bbox[0], src_rect[0])
        ox1 = min(span_bbox[2], src_rect[2])
        if ox1 > ox0:
            frac0 = (ox0 - span_bbox[0]) / (span_bbox[2] - span_bbox[0])
            frac1 = (ox1 - span_bbox[0]) / (span_bbox[2] - span_bbox[0])
            i0 = max(0, min(len(cleaned), int(frac0 * len(cleaned))))
            i1 = max(i0, min(len(cleaned), int(frac1 * len(cleaned)) + 1))
            region = cleaned[max(0, i0 - 2):min(len(cleaned), i1 + 2)]
            found = _PDF_CALLOUT_TOKEN_RE.search(region)
            if found:
                abs_start = max(0, i0 - 2) + found.start(1)
                abs_end = max(0, i0 - 2) + found.end(1)
                match = (abs_start, abs_end, found.group(1))
    if match is None:
        found = _PDF_CALLOUT_TOKEN_RE.search(cleaned)
        if found and len(found.group(1)) <= 4:
            match = (found.start(1), found.end(1), found.group(1))
    if match is None:
        return html.escape(cleaned) + " "
    start, end, token = match
    return (
        html.escape(cleaned[:start])
        + f"@@F_S|{note_id}|SUP@@{html.escape(token)}@@F_OFF_SUP@@"
        + html.escape(cleaned[end:])
        + " "
    )


# Yield lines from a PDF text block dict
def _pdf_iter_block_lines(block):
    return block.get("lines", []) if isinstance(block, dict) else []


# Extract plain text and font size from a PDF line dict
def _pdf_line_text_and_size(line):
    parts = []
    sizes = []
    for span in line.get("spans", []):
        raw = (span.get("text") or "").replace("\uf0b7", "").replace("\uf020", "")
        if not raw.strip():
            continue
        parts.append(raw)
        sizes.append(float(span.get("size") or 0))
    return "".join(parts), (max(sizes) if sizes else 0.0)


# Compute average font size and bbox for a rendered HTML element
def _pdf_element_text_metrics(element):
    block = element.get("block") or {}
    parts = []
    sizes = []
    for line in _pdf_iter_block_lines(block):
        text, size = _pdf_line_text_and_size(line)
        if text.strip():
            parts.append(text)
            sizes.append(size)
    joined = " ".join(" ".join(parts).split())
    max_size = max(sizes) if sizes else 0.0
    min_size = min(sizes) if sizes else 0.0
    return joined, max_size, min_size


# True if text appears to be a footnote (small font, bottom of page)
def _pdf_text_is_footnote_like(text: str, max_fontsize: float, bbox, page_height: Optional[float] = None) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 360:
        return False
    height = (bbox[3] - bbox[1]) if bbox else 0.0
    if height > 96 and len(cleaned) > 180:
        return False
    starts = bool(_PDF_FOOTNOTE_START_RE.match(cleaned))
    small = max_fontsize > 0 and max_fontsize <= 10.5
    near_bottom = bool(page_height and bbox and bbox[1] > page_height * 0.62)
    return starts or (small and len(cleaned) < 220) or (near_bottom and starts)


# True if text is main story content (large enough, wide bbox)
def _pdf_text_is_story(text: str, bbox, max_fontsize: float) -> bool:
    cleaned = (text or "").strip()
    height = (bbox[3] - bbox[1]) if bbox else 0.0
    if _PDF_FOOTNOTE_START_RE.match(cleaned) and len(cleaned) < 280:
        return False
    if len(cleaned) > 220:
        return True
    if height > 72 and len(cleaned) > 90 and max_fontsize >= 11:
        return True
    return False


# True if PDF line is visually footnote-sized relative to body
def _pdf_line_is_footnote(text: str, size: float, body_size: float) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if _PDF_FOOTNOTE_START_RE.match(cleaned):
        return True
    if body_size and size and size <= body_size * 0.88 and len(cleaned) < 240:
        return True
    if size <= 9.5 and len(cleaned) < 240:
        return True
    return False


# Rebuild a PDF text block from a filtered set of lines
def _pdf_rebuild_text_element(element, lines):
    if not lines:
        return None
    xs0 = min(line["bbox"][0] for line in lines)
    ys0 = min(line["bbox"][1] for line in lines)
    xs1 = max(line["bbox"][2] for line in lines)
    ys1 = max(line["bbox"][3] for line in lines)
    block = dict(element.get("block") or {})
    block["lines"] = lines
    block["bbox"] = (xs0, ys0, xs1, ys1)
    return {"type": "text", "bbox": (xs0, ys0, xs1, ys1), "block": block}


# Separate story blocks from footnote destination blocks
def _pdf_split_footnote_dest_blocks(
    elements: List[Dict[str, Any]],
    dest_notes: List[Dict[str, Any]],
    page_height: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Split mixed story+note blocks so dest wrapping does not eat chapter text."""
    dest_ys_by_index: Dict[int, List[float]] = {}
    for note in dest_notes:
        dest_point = note.get("dst_point")
        if not dest_point:
            continue
        for index, element in enumerate(elements):
            if element.get("type") != "text":
                continue
            if _pdf_point_in_bbox(dest_point, element["bbox"], pad=6.0):
                dest_ys_by_index.setdefault(index, []).append(dest_point[1])

    rebuilt: List[Dict[str, Any]] = []
    for index, element in enumerate(elements):
        split_ys = sorted(set(dest_ys_by_index.get(index) or []))
        if not split_ys or element.get("type") != "text":
            rebuilt.append(element)
            continue
        text, max_size, _ = _pdf_element_text_metrics(element)
        if not _pdf_text_is_story(text, element["bbox"], max_size) and _pdf_text_is_footnote_like(
            text, max_size, element["bbox"], page_height
        ):
            rebuilt.append(element)
            continue
        lines = list(_pdf_iter_block_lines(element.get("block") or {}))
        if not lines:
            rebuilt.append(element)
            continue
        sizes = [_pdf_line_text_and_size(line)[1] for line in lines]
        body_size = max(sizes) if sizes else max_size
        cut_at = None
        for dest_y in split_ys:
            for line_i, line in enumerate(lines):
                line_text, line_size = _pdf_line_text_and_size(line)
                line_bbox = line.get("bbox")
                at_or_below = line_bbox and line_bbox[1] >= dest_y - 8
                contains = _pdf_point_in_bbox((element["bbox"][0], dest_y), line_bbox, pad=4.0) if line_bbox else False
                if (at_or_below or contains) and _pdf_line_is_footnote(line_text, line_size, body_size):
                    cut_at = line_i if cut_at is None else min(cut_at, line_i)
                    break
            if cut_at is None:
                for line_i, line in enumerate(lines):
                    line_bbox = line.get("bbox")
                    if line_bbox and line_bbox[1] >= dest_y - 2 and _pdf_line_is_footnote(
                        _pdf_line_text_and_size(line)[0],
                        _pdf_line_text_and_size(line)[1],
                        body_size,
                    ):
                        cut_at = line_i
                        break
        if cut_at is None or cut_at <= 0:
            # Dest landed in a story block with no footnote-like suffix: keep as body.
            rebuilt.append(element)
            continue
        body_el = _pdf_rebuild_text_element(element, lines[:cut_at])
        note_el = _pdf_rebuild_text_element(element, lines[cut_at:])
        if body_el:
            rebuilt.append(body_el)
        if note_el:
            rebuilt.append(note_el)
    return rebuilt


# Score how close a block is to a footnote destination point
def _pdf_dest_score(element, dest_point, page_height: Optional[float] = None):
    bbox = element.get("bbox")
    text, max_size, _ = _pdf_element_text_metrics(element)
    if not text:
        return None
    contains = _pdf_point_in_bbox(dest_point, bbox, pad=3.0)
    dy = abs(bbox[1] - dest_point[1])
    dx = abs(bbox[0] - dest_point[0])
    if not contains and dy > 36:
        return None
    fn_like = _pdf_text_is_footnote_like(text, max_size, bbox, page_height)
    story = _pdf_text_is_story(text, bbox, max_size)
    if story and not fn_like:
        return None
    if not contains and not fn_like:
        return None
    dist = dy + dx * 0.25
    if contains:
        dist *= 0.12
    if fn_like:
        dist *= 0.35
    else:
        dist += 40.0
    return dist


# Assign each dest note to the nearest block below it on page
def _pdf_assign_dest_notes(
    elements: List[Dict[str, Any]],
    dest_notes: List[Dict[str, Any]],
    page_height: Optional[float] = None,
):
    """Map each dest note to a footnote-like text element, never a chapter block."""
    assigned: Dict[int, Dict[str, Any]] = {}
    used = set()
    text_indices = [i for i, element in enumerate(elements) if element.get("type") == "text"]
    for note in dest_notes:
        dest_point = note.get("dst_point")
        if not dest_point or note["id"] in used:
            continue
        scored = []
        for index in text_indices:
            if index in assigned:
                continue
            score = _pdf_dest_score(elements[index], dest_point, page_height)
            if score is not None:
                scored.append((score, index))
        if not scored:
            continue
        scored.sort(key=lambda item: item[0])
        hit = scored[0][1]
        assigned[hit] = note
        used.add(note["id"])
    return assigned


# Render a PDF block's spans into HTML markup with footnote links
def _pdf_block_span_markup(block, src_notes: List[Dict[str, Any]], page_index: Optional[int] = None):
    collected = []
    max_fontsize = 0.0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            raw = span.get("text") or ""
            size = float(span.get("size") or 0)
            if size > max_fontsize:
                max_fontsize = size
            cleaned = raw.replace("\uf0b7", "").replace("\uf020", "")
            if not cleaned.strip():
                continue
            collected.append({
                "bbox": span.get("bbox"),
                "text": cleaned,
                "flags": int(span.get("flags") or 0),
            })

    winners: Dict[str, tuple] = {}
    for span_i, span in enumerate(collected):
        for note in src_notes:
            score = _pdf_note_src_score(span["bbox"], span["text"], span["flags"], note, page_index)
            if score is None:
                continue
            prev = winners.get(note["id"])
            if prev is None or score > prev[0]:
                winners[note["id"]] = (score, span_i, note)

    wrap_at: Dict[int, Dict[str, Any]] = {}
    for _note_id, (_score, span_i, note) in winners.items():
        wrap_at[span_i] = note

    pieces = []
    plain_parts = []
    for span_i, span in enumerate(collected):
        cleaned = span["text"]
        flags = span["flags"]
        note = wrap_at.get(span_i)
        if note:
            src_rect = None
            for src_page, rect in note.get("src_rects") or [(note["src_page"], note["src_rect"])]:
                if page_index is not None and src_page != page_index:
                    continue
                src_rect = rect
                if _pdf_rects_intersect(span["bbox"], rect, pad=0.25):
                    break
            pieces.append(
                _pdf_wrap_callout_markup(cleaned, note["id"], span["bbox"], src_rect, flags)
            )
        elif flags & 1:
            pieces.append(f"<sup>{html.escape(cleaned)}</sup> ")
        else:
            pieces.append(html.escape(cleaned) + " ")
        plain_parts.append(cleaned)
    markup = re.sub(r" {2,}", " ", "".join(pieces)).strip()
    plain = " ".join(" ".join(plain_parts).split()).strip()
    if plain.startswith("•"):
        plain = plain[1:].strip()
    return plain, markup.strip(), max_fontsize


# Master PDF ingestion pipeline: extract text/spans, detect footnotes, and render pages
@router.post("/api/convert/pdf")
async def convert_pdf(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    import shutil
    try: import pymupdf
    except ImportError: raise HTTPException(status_code=500, detail="PyMuPDF library not installed.")
        
    from fastapi import HTTPException
    from logic.smart_content_detector import detect_strict_scene_break

    if not file.filename.lower().endswith(".pdf"): raise HTTPException(status_code=400, detail="Not a PDF file")

    doc_id = id
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    temp_pdf = book_dir / "temp.pdf"

    try:
        with open(temp_pdf, "wb") as f:
            content = await file.read()
            f.write(content)

        try: doc = pymupdf.open(str(temp_pdf))
        except Exception:
            shutil.rmtree(book_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Cannot read PDF file (corrupted or DRM protected)")

        book_lang = language_from_pdf_doc(doc)

        total_text_len = sum(len(doc[i].get_text()) for i in range(min(5, len(doc))))
        if len(doc) > 0 and total_text_len < 50:
            raise HTTPException(status_code=400, detail="Scanned (image-only) PDFs are not supported for TTS.")

        raw_toc = doc.get_toc()
        toc_map = []
        if raw_toc:
            for item in raw_toc:
                lvl, title, page_num = item
                toc_map.append({"title": title, "level": lvl, "page_index": max(0, page_num - 1), "anchor_id": None})

        allow_scene_breaks = False
        if len(doc) > 0:
            first_page_images = doc[0].get_images(full=True)
            if first_page_images: allow_scene_breaks = True

        pdf_notes = _pdf_collect_goto_notes(doc)

        pages = []
        image_map = {}
        image_counter = 1
        global_sentence_idx = 0
        held_text = "" 
        paragraph_terminators = (".", "!", "?", "…", "。", "！", "？", "”", '"', "’", "'", "」", "』")

        for page_index in range(len(doc)):
            page = doc[page_index]
            page_html = ""
            elements = []
            page_height = float(page.rect.y1) if getattr(page, "rect", None) is not None else None
            src_notes = [
                note for note in pdf_notes
                if any(
                    src_page == page_index
                    for src_page, _ in note.get("src_rects") or [(note["src_page"], note["src_rect"])]
                )
            ]
            dest_notes = [note for note in pdf_notes if note.get("dst_page") == page_index]
            
            table_bboxes = []
            if hasattr(page, "find_tables"):
                for tab in page.find_tables():
                    elements.append({"type": "table", "bbox": tab.bbox, "data": tab.extract()})
                    table_bboxes.append(tab.bbox)

            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                b_bbox = block["bbox"]
                is_in_table = False
                for t_bbox in table_bboxes:
                    cx = (b_bbox[0] + b_bbox[2]) / 2
                    cy = (b_bbox[1] + b_bbox[3]) / 2
                    if t_bbox[0] <= cx <= t_bbox[2] and t_bbox[1] <= cy <= t_bbox[3]:
                        is_in_table = True
                        break
                if not is_in_table:
                    elements.append({"type": "text" if block["type"] == 0 else "image", "bbox": b_bbox, "block": block})
            
            elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
            elements = _pdf_split_footnote_dest_blocks(elements, dest_notes, page_height)
            dest_assignment = _pdf_assign_dest_notes(elements, dest_notes, page_height)

            for elem_index, element in enumerate(elements):
                if element["type"] == "text":
                    block = element["block"]
                    block_text, block_markup, max_fontsize = _pdf_block_span_markup(
                        block, src_notes, page_index
                    )
                    if block_text.startswith('•'): block_text = block_text[1:].strip()
                    if not block_text or block_text in ['•', '-', '·']: continue

                    dest_note = dest_assignment.get(elem_index)
                    if dest_note:
                        if held_text:
                            split_html, global_sentence_idx = master_sentence_splitter(
                                held_text, global_sentence_idx
                            )
                            page_html += split_html
                            held_text = ""
                        page_html += (
                            f'<p epub:type="footnote" id="{html.escape(dest_note["id"], quote=True)}">'
                            f"{html.escape(block_text)}</p>"
                        )
                        continue

                    is_header = False
                    if max_fontsize > 14 and len(block_text) < 100 and not block_text.endswith(paragraph_terminators):
                        is_header = True
                        
                    is_scene_break = detect_strict_scene_break(block_text, allow_scene_breaks)

                    if (is_header or is_scene_break) and held_text:
                        split_html, global_sentence_idx = master_sentence_splitter(
                            held_text, global_sentence_idx
                        )
                        page_html += split_html
                        held_text = ""

                    emit_markup = block_markup or html.escape(block_text)
                    if not is_header and not is_scene_break and held_text:
                        held_visible = _pdf_visible_plain(held_text)
                        if held_visible.endswith("-") and not held_visible.endswith(" -"):
                            held_cut = held_text[:-1] if held_text.endswith("-") else held_text
                            emit_markup = held_cut + (block_markup or html.escape(block_text))
                            block_text = held_visible[:-1] + block_text
                        else:
                            emit_markup = held_text + " " + (block_markup or html.escape(block_text))
                            block_text = held_visible + " " + block_text
                        held_text = ""

                    if not is_header and not is_scene_break and not block_text.endswith(paragraph_terminators):
                        held_text = emit_markup
                        continue

                    if is_scene_break:
                        page_html += f"<s>{html.escape(block_text)}</s>"
                    elif is_header:
                        safe_header = html.escape(block_text)
                        page_html += f'<h2 id="s_{global_sentence_idx}">{safe_header}</h2>'
                        global_sentence_idx += 1
                    else:
                        if emit_markup:
                            split_html, global_sentence_idx = master_sentence_splitter(
                                emit_markup, global_sentence_idx
                            )
                            page_html += split_html

                elif element["type"] == "image":
                    if held_text:
                        split_html, global_sentence_idx = master_sentence_splitter(
                            held_text, global_sentence_idx
                        )
                        page_html += split_html
                        held_text = ""
                        
                    block = element["block"]
                    try:
                        width = block.get("width", 0)
                        height = block.get("height", 0)
                        if width < 50 or height < 50: continue
                            
                        image_bytes = block.get("image")
                        image_ext = block.get("ext", "jpg")
                        if not image_bytes or len(image_bytes) < 1024: continue
                            
                        image_filename = f"image_{image_counter}.{image_ext}"
                        image_path = book_dir / image_filename
                        
                        with open(image_path, "wb") as img_file: img_file.write(image_bytes)
                        image_map[str(image_counter)] = image_filename
                        assigned_id = str(image_counter)
                        image_counter += 1
                        page_html += _html_norm.epub_image_html(
                            f"/api/library/image/{doc_id}/{assigned_id}",
                            "lazy",
                            'style="max-width:100%; height:auto;"',
                        )
                    except Exception: pass

                elif element["type"] == "table":
                    if held_text:
                        split_html, global_sentence_idx = master_sentence_splitter(
                            held_text, global_sentence_idx
                        )
                        page_html += split_html
                        held_text = ""
                        
                    table_html = "<table class='pdf-table' border='1' style='border-collapse: collapse; width: 100%; margin: 10px 0;'>"
                    for row in element["data"]:
                        table_html += "<tr>"
                        for cell in row:
                            cell_text = str(cell) if cell else ""
                            if cell_text.strip():
                                safe_text = html.escape(cell_text.strip())
                                cell_html, global_sentence_idx = master_sentence_splitter(
                                    safe_text, global_sentence_idx, "td"
                                )
                                table_html += cell_html
                            else: table_html += "<td></td>"
                        table_html += "</tr>"
                    table_html += "</table>"
                    page_html += table_html

            if page_html.strip():
                pages.append(f'<div class="pdf-page">{page_html}</div>')
            else:
                pages.append(f'<div class="pdf-page"><p id="s_{global_sentence_idx}">[Blank Page]</p></div>')
                global_sentence_idx += 1

        if held_text:
            split_html, global_sentence_idx = master_sentence_splitter(
                held_text, global_sentence_idx
            )
            if split_html:
                if pages: pages[-1] = pages[-1].replace('</div>', f'{split_html}</div>')
                else: pages.append(f'<div class="pdf-page">{split_html}</div>')

        if not book_lang:
            try:
                sample = "".join(doc[i].get_text() for i in range(min(3, len(doc))))
            except Exception:
                sample = ""
            book_lang = language_from_text_heuristic(sample)

        doc.close()
        temp_pdf.unlink(missing_ok=True)
        
        if not toc_map: toc_map = _epub_norm.generate_toc(pages)

        _epub_norm.assign_toc_target_ids(pages, toc_map)
        pages = _html_norm.apply_image_loading(pages)

        if not book_lang:
            book_lang = language_from_pages(pages)

        return {
            "pages": pages,
            "image_map": image_map,
            "toc_map": toc_map,
            "language": book_lang,
            "bookType": "pdf",
        }

    except Exception as e:
        import traceback
        print("\n" + "="*60)
        print("🚨 FATAL PDF EXTRACTION CRASH 🚨")
        traceback.print_exc()
        print("="*60 + "\n")
        
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

# Normalize bookType to 'epub' or 'pdf', None otherwise
def _normalize_book_type(value: Any) -> Optional[str]:
    kind = str(value or "").strip().lower()
    if kind in ("pdf", "epub"):
        return kind
    return None


# Catalog used to be one userdata/library.json array: each row mixed identity
# (id, fileName, bookType) with reading progress (currentPage, lastSentenceId, …).
# That file is migrated away at boot. Canonical store is now two sidecar folders:
#   peek EPUB  -> userdata/library/<id>/info.json + progress.json
#   PDF/legacy -> userdata/metadata/<id>/info.json + progress.json
# redirect.py registers the same paths first; these handlers are the fallback.
@router.get("/api/library")
def get_library():
    from ..logic.memories import list_peek_catalog, list_pdf_catalog
    try:
        return list_peek_catalog() + list_pdf_catalog()
    except Exception:
        return []


# POST used to merge LibraryItem into library.json (skip peek).
# PDF/legacy now write userdata/metadata/<id>/info.json + progress.json instead.
@router.post("/api/library")
async def save_library_item(item: LibraryItem):
    from ..logic.memories import is_peek_book, upsert_pdf_sidecar
    if is_peek_book(item.id):
        return {"status": "ok"}
    incoming = item.model_dump()
    incoming["bookType"] = _normalize_book_type(incoming.get("bookType"))
    try:
        upsert_pdf_sidecar(incoming)
    except Exception as e:
        print(f"[Library] Failed saving metadata sidecar for {item.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save library item")
    return {"status": "ok"}


# DELETE used to drop the library.json row, rmtree content/<id>/, then drop peek sidecars.
# delete_library_book now removes peek sidecar, PDF sidecar, and extracted content.
@router.delete("/api/library/{doc_id}")
async def delete_library_item(doc_id: str):
    from ..logic.memories import delete_library_book
    async with _library_lock:
        if not delete_library_book(doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        return {"status": "deleted"}

# Load and return parsed book content JSON
@router.get("/api/library/content/{doc_id}")
def get_content(doc_id: str):
    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
    return data

# Save parsed book content JSON to book directory
@router.post("/api/library/content")
async def save_content(request: Request):
    data = await request.json()
    doc_id = data['id']
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    safe_save_json(book_dir / f"{doc_id}.json", data)
    return {"status": "ok"}

# Serve extracted book image file from content directory
@router.get("/api/library/image/{doc_id}/{image_id}")
def get_image(doc_id: str, image_id: str):
    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
    image_map = data.get("image_map", {})
    filename = image_map.get(image_id)

    if not filename: raise HTTPException(status_code=404, detail="Image not mapped")
    image_path = content_dir / doc_id / filename
    if not image_path.exists(): raise HTTPException(status_code=404, detail="Image missing")
    return FileResponse(image_path)


# Search across book pages for a query phrase
@router.get("/api/library/search/{doc_id}")
def search_book(doc_id: str, q: str, match_case: bool = False, whole_word: bool = False):
    if not q or len(q) < 2: return {"results": [], "total_matches": 0, "query": q}

    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)

    pages = data.get("pages", [])
    results = []
    total_matches = 0
    
    q_norm = q.replace('‘', "'").replace('’', "'").replace('´', "'").replace('`', "'").replace('“', '"').replace('”', '"')
    flags = 0 if match_case else re.IGNORECASE
    escaped_q = _html_norm.escape_search_query(q_norm)
    if not escaped_q:
        return {"results": [], "total_matches": 0, "query": q}
    pattern_str = rf"\b{escaped_q}\b" if whole_word else escaped_q
    
    try: pattern = re.compile(pattern_str, flags)
    except Exception: return {"results": [], "total_matches": 0, "query": q}

    for page_index, page_html in enumerate(pages):
        page_text = _html_norm.html_to_search_text(page_html)
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

# Progress used to patch currentPage / lastSentence* onto the library.json row.
# Same fields now live in that book's progress.json (peek or PDF sidecar). PDF has no shelf_status.
@router.post("/api/library/progress/{doc_id}")
async def update_book_progress_checkpoint(doc_id: str, payload: ProgressUpdatePayload):
    from ..logic.memories import (
        is_peek_book,
        is_pdf_sidecar_book,
        load_any_progress,
        save_book_progress,
    )
    peek = is_peek_book(doc_id)
    pdf = (not peek) and is_pdf_sidecar_book(doc_id)
    if not peek and not pdf:
        raise HTTPException(status_code=404, detail="Requested record entry missing.")
    prev = load_any_progress(doc_id) or {}
    entry = {
        "currentPage": payload.currentPage,
        "lastSentenceId": payload.lastSentenceId,
        "lastSentenceIndex": payload.lastSentenceIndex,
        "lastAccessed": payload.lastAccessed,
        "current_page": payload.current_page,
        "total_pages": payload.total_pages,
        "progress_percent": payload.progress_percent,
        "disable_br": prev.get("disable_br", False) if payload.disable_br is None else bool(payload.disable_br),
    }
    try:
        save_book_progress(doc_id, entry, kind="pdf" if pdf else "peek")
    except Exception as io_error:
        print(f"[Error] Failed to auto-save progress.json: {io_error}")
        raise HTTPException(status_code=500, detail=f"Database sync failure: {str(io_error)}")
    return {"status": "success", "message": f"Checkpoint saved for {doc_id}"}