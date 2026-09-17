"""
In-Memory EPUB Data Interception Manager (memories.py)
------------------------------------------------------
Intercepts raw data coming from the native C++ peeker binary, manages the
3-Tier Hybrid Memory Model, runs in-memory DOM normalization (Ruby protection,
formatting markers, scene breaks, footnote callout linking, scoped block IDs),
and saves ONLY lightweight book sidecars (userdata/library/<doc_id>/info.json).

Key 
- ZERO images written to disk (all images stream on-demand via Tier 3).
- ZERO monolithic content.json written to disk.
- Pure in-memory chapter rendering and text buffering (Tier 1 & Tier 2).
"""

import os
import re
import time
import json
import uuid
import html
import posixpath
import urllib.parse
import threading
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple, Set

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

# Import shared application paths and utilities
try:
    from ..config import library_dir, library_file, metadata_dir, content_dir
    from ..utils import safe_save_json, safe_init_json, save_json_rotate_old
except (ImportError, ValueError):
    userdata_dir = Path(__file__).resolve().parent.parent.parent / "userdata"
    metadata_dir = userdata_dir / "metadata"
    library_dir = userdata_dir / "library"
    content_dir = userdata_dir / "content"
    library_file = userdata_dir / "library.json"
    def safe_save_json(path, data, indent=2):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    def save_json_rotate_old(path, data, indent=2):
        path = Path(path)
        old_path = Path(str(path) + ".old")
        if path.exists():
            try:
                import shutil
                shutil.copy2(path, old_path)
            except OSError:
                pass
        safe_save_json(path, data, indent=indent)

# Import pure normalizer functions
try:
    from .html_normalizer import (
        standardize_footnotes,
        _normalize_epub_html,
        pre_parse_clean,
    )
    from .smart_content_detector import detect_strict_scene_break
except (ImportError, ValueError):
    from html_normalizer import (
        standardize_footnotes,
        _normalize_epub_html,
        pre_parse_clean,
    )
    from smart_content_detector import detect_strict_scene_break

# Legacy unzip pipeline helpers (imported, not copied)
try:
    from ..routers.library import (
        force_formatting_markers,
        restore_inline_markers,
        assign_epub_block_ids,
        assign_toc_target_ids,
        escape_search_query,
        html_to_search_text,
        first_css_url,
        epub_image_html,
        scene_break_open_tag,
        sandwich_allows_scene_break,
        strip_scene_break_inner_wrappers,
        climb_empty_ornament_wrapper,
        _drop_scene_break_target_ids,
        nodes_in_document_order,
        find_parent,
        HEADING_TAGS,
        CSS_ORNAMENT_KEYWORDS,
        ORNAMENT_KEYWORDS,
        clean_scene_break_contents,
        apply_image_loading,
    )
except (ImportError, ValueError):
    from routers.library import (
        force_formatting_markers,
        restore_inline_markers,
        assign_epub_block_ids,
        assign_toc_target_ids,
        escape_search_query,
        html_to_search_text,
        first_css_url,
        epub_image_html,
        scene_break_open_tag,
        sandwich_allows_scene_break,
        strip_scene_break_inner_wrappers,
        climb_empty_ornament_wrapper,
        _drop_scene_break_target_ids,
        nodes_in_document_order,
        find_parent,
        HEADING_TAGS,
        CSS_ORNAMENT_KEYWORDS,
        ORNAMENT_KEYWORDS,
        clean_scene_break_contents,
        apply_image_loading,
    )

# ---------------------------------------------------------------------------
# Native Engine Bridge (in-process shared library)
# ---------------------------------------------------------------------------
import platform
import ctypes
import struct

_peeker_lib = None
_peeker_lock = threading.Lock()


def get_engine_binary() -> Path:
    """Resolves the compiled native zipstream library for the host OS and architecture."""
    engine_dir = Path(__file__).resolve().parent.parent / "engine"
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        binary = engine_dir / "zipstream-win-x64.dll"
        if not binary.exists():
            binary = engine_dir / "zipstream.dll"
    elif system == "linux":
        binary = engine_dir / "zipstream-linux-x64.so"
        if not binary.exists():
            binary = engine_dir / "zipstream-linux-x64"
    elif system == "darwin":
        if "arm" in machine or "aarch64" in machine:
            binary = engine_dir / "zipstream-darwin-arm64.dylib"
            if not binary.exists():
                binary = engine_dir / "zipstream-darwin-arm64"
        else:
            binary = engine_dir / "zipstream-darwin-x64.dylib"
            if not binary.exists():
                binary = engine_dir / "zipstream-darwin-x64"
    else:
        raise OSError(f"Unsupported OS platform: {system} ({machine})")

    if not binary.exists():
        raise FileNotFoundError(f"Native zipstream library not found: {binary}")
    return binary


def _load_peeker_lib():
    global _peeker_lib
    if _peeker_lib is not None:
        return _peeker_lib
    lib = ctypes.CDLL(os.fspath(get_engine_binary()))
    lib.zipstream_manifest.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.zipstream_manifest.restype = ctypes.c_int
    lib.zipstream_single.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.zipstream_single.restype = ctypes.c_int
    lib.zipstream_stream_html.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.zipstream_stream_html.restype = ctypes.c_int
    lib.zipstream_free.argtypes = [ctypes.c_void_p]
    lib.zipstream_free.restype = None
    _peeker_lib = lib
    return lib


def _path_utf8(epub_path: Path | str) -> bytes:
    return os.fspath(epub_path).encode("utf-8")


def source_epub_is_present(epub_path: Path | str | None) -> bool:
    """True when the bound .epub still exists as a file at the stored path."""
    if not epub_path:
        return False
    try:
        return Path(epub_path).is_file()
    except OSError:
        return False


def _peeker_call(fn, *args) -> bytes:
    lib = _load_peeker_lib()
    out = ctypes.c_void_p()
    n = ctypes.c_int(0)
    with _peeker_lock:
        rc = fn(*args, ctypes.byref(out), ctypes.byref(n))
        if rc != 0:
            if out.value:
                lib.zipstream_free(out)
            raise RuntimeError(f"zipstream returned {rc}")
        try:
            return ctypes.string_at(out, n.value) if n.value else b""
        finally:
            if out.value:
                lib.zipstream_free(out)


def _raw_for_href(raw_htmls: Optional[Dict[str, bytes]], href: str) -> Optional[bytes]:
    """Resolve a spine href against a streamed HTML map, including basename fallback."""
    if not raw_htmls or not href:
        return None
    raw = raw_htmls.get(href)
    if raw:
        return raw
    base = posixpath.basename(href).lower()
    for key, value in raw_htmls.items():
        if posixpath.basename(key).lower() == base:
            return value
    return None


def call_peeker_manifest(epub_path: Path | str) -> Dict[str, Any]:
    """Calls peeker_manifest and returns the parsed JSON file inventory."""
    raw = _peeker_call(_load_peeker_lib().zipstream_manifest, _path_utf8(epub_path))
    return json.loads(raw.decode("utf-8"))


def call_peeker_single(epub_path: Path | str, inner_path: str) -> bytes:
    """Calls peeker_single and returns raw uncompressed bytes."""
    return _peeker_call(
        _load_peeker_lib().zipstream_single,
        _path_utf8(epub_path),
        inner_path.encode("utf-8"),
    )


def call_peeker_stream_html(epub_path: Path | str) -> Dict[str, bytes]:
    """
    Calls peeker_stream_html and parses the framed binary stream.
    Format: [uint32_t name_len][name string][uint32_t data_len][decompressed bytes]
    """
    raw = _peeker_call(_load_peeker_lib().zipstream_stream_html, _path_utf8(epub_path))
    chapters: Dict[str, bytes] = {}
    offset = 0
    raw_len = len(raw)

    while offset + 8 <= raw_len:
        name_len = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        if offset + name_len > raw_len:
            break
        name = raw[offset:offset + name_len].decode("utf-8", errors="ignore")
        offset += name_len
        if offset + 4 > raw_len:
            break
        data_len = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        if offset + data_len > raw_len:
            break
        data = raw[offset:offset + data_len]
        offset += data_len
        chapters[name] = data

    return chapters


# ---------------------------------------------------------------------------
# DOM Helpers (peek-only image/scene-break rewrites)
# ---------------------------------------------------------------------------
def replace_with_html(node: Optional[Node], markup: str) -> None:
    """Replace a node with parsed markup in the current DOM tree."""
    if not node or node.parent is None:
        return
    destination_parent = node.parent
    marker = "data-selectolax-replacement-root"
    fragment = HTMLParser(f"<body><div {marker}>{markup}</div></body>")
    wrapper = fragment.css_first(f"[{marker}]")
    if wrapper is None:
        node.decompose()
        return
    node.replace_with(wrapper)
    moved_wrapper = destination_parent.css_first(f"[{marker}]")
    if moved_wrapper is not None:
        moved_wrapper.unwrap()


def safe_unwrap(node: Optional[Node]) -> None:
    if not node or node.parent is None or node.tag in ("body", "html", "head", None):
        return
    node.unwrap()


# ---------------------------------------------------------------------------
# Scene Break & Image Tag Interception
# ---------------------------------------------------------------------------
def sanitize_image_id(filename: str) -> str:
    """Creates a clean URL-safe image ID without extension collisions."""
    clean = posixpath.basename(filename).split("?")[0].split("#")[0]
    safe = re.sub(r'[\\/*?:"<>|]', "", clean.replace("/", "_").replace("\\", "_"))
    return safe or f"img_{uuid.uuid4().hex[:8]}.jpg"


def resolve_image_map_entry(
    image_map: Dict[str, str],
    needle: str,
) -> Optional[Tuple[str, str]]:
    """Exact image id or zip path first; basename only if exactly one map entry matches."""
    clean = urllib.parse.unquote(str(needle or "")).strip().replace("\\", "/")
    if not clean or not image_map:
        return None
    if clean in image_map:
        return clean, image_map[clean]

    lower = clean.lower().lstrip("/")
    lower_base = posixpath.basename(lower)

    id_hits: List[str] = []
    path_hits: List[str] = []
    for img_id, zip_path in image_map.items():
        z_norm = str(zip_path or "").replace("\\", "/").lower().lstrip("/")
        if img_id.lower() == lower:
            id_hits.append(img_id)
        if z_norm == lower:
            path_hits.append(img_id)

    if len(id_hits) == 1:
        k = id_hits[0]
        return k, image_map[k]
    if len(path_hits) == 1:
        k = path_hits[0]
        return k, image_map[k]

    base_ids: List[str] = []
    for img_id, zip_path in image_map.items():
        z_base = posixpath.basename(str(zip_path or "").replace("\\", "/")).lower()
        k_base = posixpath.basename(img_id).lower()
        if z_base == lower_base or k_base == lower_base:
            base_ids.append(img_id)
    unique_ids = list(dict.fromkeys(base_ids))
    if len(unique_ids) == 1:
        k = unique_ids[0]
        return k, image_map[k]
    return None


def resolve_map_key(keys, needle: str) -> Optional[str]:
    """Exact key, then case-insensitive, then unique basename among mapping keys."""
    clean = urllib.parse.unquote(str(needle or "")).strip()
    if not clean:
        return None
    key_list = list(keys)
    if clean in key_list:
        return clean
    lower = clean.lower()
    ci = [k for k in key_list if k.lower() == lower]
    if len(ci) == 1:
        return ci[0]
    base = posixpath.basename(lower)
    base_hits = [k for k in key_list if posixpath.basename(k).lower() == base]
    unique = list(dict.fromkeys(base_hits))
    if len(unique) == 1:
        return unique[0]
    return None


_NARRATIVE_CHARS = re.compile(
    r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]"
)


def html_has_narrative_besides_media(markup: str) -> bool:
    """True when leftover readable text remains after stripping media tags."""
    if not markup:
        return False
    stripped = re.sub(
        r"<(?:svg|picture)\b[^>]*>.*?</(?:svg|picture)>", " ", markup, flags=re.I | re.S
    )
    stripped = re.sub(r"<(?:img|image)\b[^>]*/?>", " ", stripped, flags=re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    leftover = re.sub(r"\s+", " ", stripped).strip()
    return bool(leftover and _NARRATIVE_CHARS.search(leftover))


_MEDIA_TAG_RE = re.compile(r"<(?:img|image|svg|picture|video|canvas)\b", re.I)
_RESOURCE_ATTR_RE = re.compile(
    r"""(?:xlink:href|href|srcset|src)\s*=\s*(["'])(.*?)\1""",
    re.I | re.S,
)


def _strip_resource_ref(raw: str) -> str:
    text = urllib.parse.unquote(str(raw or "")).strip()
    if text.lower().startswith("url(") and text.endswith(")"):
        text = text[4:-1].strip().strip("\"'")
    if "," in text and " " in text:
        text = text.split(",")[0].strip().split()[0]
    return text.split("#")[0].split("?")[0].strip()


def image_src_from_node(node: Optional[Node]) -> str:
    """Read img/image href even when lexbor drops namespaced xlink:href."""
    if node is None:
        return ""
    attrs = node.attributes or {}
    for key in ("src", "xlink:href", "href", "srcset"):
        cleaned = _strip_resource_ref(attrs.get(key) or "")
        if cleaned:
            return cleaned
    for key, value in attrs.items():
        lower = str(key or "").lower().replace("{http://www.w3.org/1999/xlink}", "xlink:")
        if not any(token in lower for token in ("href", "src")):
            continue
        cleaned = _strip_resource_ref(value or "")
        if cleaned:
            return cleaned
    html_frag = (node.html or "") + " "
    parent = find_parent_tag(node, ("svg", "picture"))
    if parent is not None:
        html_frag += parent.html or ""
    for match in _RESOURCE_ATTR_RE.finditer(html_frag):
        cleaned = _strip_resource_ref(match.group(2) or "")
        if cleaned and not cleaned.startswith(("#", "data:")):
            return cleaned
    return ""


def raw_chapter_worth_keeping(raw: Optional[bytes]) -> bool:
    """Keep spine HTML that still has media or readable text (drop blank verso pages)."""
    if not raw:
        return False
    text = raw.decode("utf-8", errors="ignore")
    if _MEDIA_TAG_RE.search(text) or re.search(r"<s\b", text, re.I) or re.search(r"<h[1-6]\b", text, re.I):
        return True
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    stripped = re.sub(r"<style\b[^>]*>.*?</style>", " ", stripped, flags=re.I | re.S)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    leftover = re.sub(r"[\s\xa0]+", " ", stripped).strip()
    return bool(leftover and _NARRATIVE_CHARS.search(leftover))


def page_html_has_reader_content(page_html: str) -> bool:
    """Same keep-gate as the unzip importer: skip empty <body></body> pages."""
    if not page_html:
        return False
    if re.search(r"""id=["']s_\d+""", page_html) or "<img" in page_html or "<image" in page_html or "<s>" in page_html:
        return True
    residual_tree = HTMLParser(page_html)
    residual_target = residual_tree.body or residual_tree.root
    residual_text = (
        residual_target.text(separator=" ", strip=True) if residual_target is not None else ""
    )
    return bool(residual_text and _NARRATIVE_CHARS.search(residual_text))


def filter_reader_spine(spine: List[str], htmls: Dict[str, bytes]) -> List[str]:
    """Drop blank insert/verso XHTML that would serialize as <body></body>."""
    kept = [href for href in spine if raw_chapter_worth_keeping(_htmls_bytes_for(htmls, href))]
    return kept or list(spine)


def unique_image_map_id(image_map: Dict[str, str], zip_path: str) -> str:
    safe_name = sanitize_image_id(zip_path)
    existing = image_map.get(safe_name)
    if existing in (None, zip_path):
        return safe_name
    stem, ext = posixpath.splitext(safe_name)
    n = 2
    candidate = f"{stem}_{n}{ext}"
    while candidate in image_map and image_map[candidate] != zip_path:
        n += 1
        candidate = f"{stem}_{n}{ext}"
    return candidate


def build_toc_normalizer_inputs(
    toc_map: Optional[List[Dict[str, Any]]],
    spine: Optional[List[str]],
) -> Tuple[Set[str], Dict[str, List[Dict[str, str]]]]:
    titles: Set[str] = set()
    rich: Dict[str, List[Dict[str, str]]] = {}
    spine = list(spine or [])
    for entry in toc_map or []:
        title = str((entry or {}).get("title") or "").strip()
        if not title:
            continue
        clean = " ".join(title.split()).lower()
        titles.add(clean)
        href = ""
        pidx = (entry or {}).get("page_index", -1)
        try:
            pidx_i = int(pidx)
        except (TypeError, ValueError):
            pidx_i = -1
        if 0 <= pidx_i < len(spine):
            href = posixpath.basename(spine[pidx_i]).lower()
        if href:
            rich.setdefault(href, []).append({
                "title": title,
                "clean_title": clean,
                "anchor": str((entry or {}).get("anchor_id") or ""),
            })
    return titles, rich


def ensure_toc_normalizer_inputs(meta: dict) -> Tuple[Set[str], Dict[str, Any]]:
    titles = meta.get("known_toc_titles")
    rich = meta.get("rich_toc_map")
    if titles is None or not isinstance(rich, dict):
        titles_set, rich = build_toc_normalizer_inputs(meta.get("toc_map") or [], meta.get("spine") or [])
        meta["known_toc_titles"] = sorted(titles_set)
        meta["rich_toc_map"] = rich
        return titles_set, rich
    if isinstance(titles, set):
        return titles, rich
    return set(titles), rich


def _spine_next_has_header(spine: List[str], idx: int, raw_htmls: Optional[Dict[str, bytes]]) -> bool:
    if not raw_htmls:
        return False
    for lookahead in range(1, 4):
        nxt = idx + lookahead
        if nxt >= len(spine):
            break
        raw = _raw_for_href(raw_htmls, spine[nxt])
        if not raw:
            continue
        low = raw.lower()
        if b"<h1" in low or b"<h2" in low:
            return True
    return False


def _remap_page_index(old: int, spine_to_page: Dict[int, int], kept: List[int]) -> int:
    if old in spine_to_page:
        return spine_to_page[old]
    if old < 0 or not kept:
        return old
    for spine_idx in kept:
        if spine_idx >= old:
            return spine_to_page[spine_idx]
    return spine_to_page[kept[-1]]


def compact_empty_reader_pages(meta: dict, session: Dict[str, Any]) -> bool:
    """Drop post-pipeline empty bodies and remap toc/footnote/image indices."""
    spine = list(meta.get("spine") or [])
    chapters = session.get("chapters") or {}
    if not spine:
        return False
    kept: List[int] = []
    for idx, _href in enumerate(spine):
        html = chapters.get(idx)
        if html is None or page_html_has_reader_content(html):
            kept.append(idx)
    if not kept or len(kept) == len(spine):
        return False

    spine_to_page = {old: new for new, old in enumerate(kept)}
    meta["spine"] = [spine[i] for i in kept]
    session["chapters"] = {
        new: chapters[old] for new, old in enumerate(kept) if old in chapters
    }
    old_images = meta.get("chapter_images") or {}
    meta["chapter_images"] = {
        str(new): old_images.get(str(old), []) for new, old in enumerate(kept)
    }
    for entry in meta.get("toc_map") or []:
        if not isinstance(entry, dict):
            continue
        try:
            old = int(entry.get("page_index", -1))
        except (TypeError, ValueError):
            continue
        if old >= 0:
            entry["page_index"] = _remap_page_index(old, spine_to_page, kept)
    for rec in (meta.get("footnote_map") or {}).values():
        if not isinstance(rec, dict):
            continue
        for key in ("page_index", "callout_page_index"):
            try:
                old = int(rec.get(key, -1))
            except (TypeError, ValueError):
                continue
            if old >= 0:
                rec[key] = _remap_page_index(old, spine_to_page, kept)
    meta["total_pages"] = len(meta["spine"])
    meta["totalPages"] = len(meta["spine"])
    titles, rich = build_toc_normalizer_inputs(meta.get("toc_map") or [], meta["spine"])
    meta["known_toc_titles"] = sorted(titles)
    meta["rich_toc_map"] = rich
    doc_id = str(meta.get("id") or "")
    if doc_id:
        prev = load_book_progress(doc_id)
        if prev:
            try:
                old_page = int(prev.get("currentPage", 0) or 0)
            except (TypeError, ValueError):
                old_page = 0
            if old_page >= 0:
                prev["currentPage"] = _remap_page_index(old_page, spine_to_page, kept)
                try:
                    save_book_progress(doc_id, prev, rotate_old=False)
                except Exception:
                    pass
    session["meta"] = meta
    return True


def find_parent_tag(node, tags):
    """Finds nearest ancestor matching any of the specified tags."""
    if not node:
        return None
    wanted = {tags} if isinstance(tags, str) else set(tags)
    cur = node.parent
    while cur is not None:
        if cur.tag in wanted:
            return cur
        cur = cur.parent
    return None


def intercept_image_tags(tree: HTMLParser, chapter_href: str, meta: dict) -> List[Tuple[str, str]]:
    """
    Intercepts and normalizes <img src> and <image href> tags in memory:
    1. Clears wrapping <svg>, <picture>, or dead <a> tags out.
    2. Converts SVG <image> elements into clean HTML <img> tags with class='epub-image'.
    3. Wraps with loading='lazy' for standalone single-image pages and loading='eager' for mixed/scene-break pages.
    4. Rewrites src to /api/library/image/{doc_id}/{image_id}.
    5. Returns list of (image_id, zip_path) so image bytes can be loaded into RAM.
    """
    doc_id = meta["id"]
    image_map = meta.setdefault("image_map", {})
    html_dir = posixpath.dirname(chapter_href)

    chapter_root = tree.body or tree.root
    chapter_markup = (chapter_root.html if chapter_root is not None else tree.html) or ""
    chapter_mixed = html_has_narrative_besides_media(chapter_markup)
    all_imgs = list(tree.css("img, image"))
    is_standalone = (not chapter_mixed) and (len(all_imgs) == 1)
    default_loading = "lazy" if is_standalone else "eager"

    chapter_images: List[Tuple[str, str]] = []

    for img in all_imgs:
        if img.parent is None:
            continue
        src = image_src_from_node(img)
        if not src:
            # Leave publisher markup in place; never wipe a full-page SVG insert.
            continue

        clean_src = _strip_resource_ref(src)
        if clean_src.startswith("/"):
            resolved_path = posixpath.normpath(clean_src).lstrip("/")
        else:
            resolved_path = posixpath.normpath(posixpath.join(html_dir, clean_src)).lstrip("/")

        matched = resolve_image_map_entry(image_map, resolved_path)
        if not matched:
            matched = resolve_image_map_entry(image_map, posixpath.basename(resolved_path))
        if matched:
            matched_id, zip_path = matched
        else:
            matched_id = unique_image_map_id(image_map, resolved_path)
            zip_path = resolved_path
            image_map[matched_id] = zip_path

        chapter_images.append((matched_id, zip_path))
        stream_url = f"/api/library/image/{doc_id}/{urllib.parse.quote(matched_id)}"

        in_s = find_parent_tag(img, "s") is not None
        item_loading = "eager" if in_s else default_loading

        clean_img_html = f'<img src="{stream_url}" class="epub-image" loading="{item_loading}" alt="Illustration">'

        svg_parent = find_parent_tag(img, ("svg", "picture"))
        if svg_parent:
            replace_with_html(svg_parent, clean_img_html)
        elif img.tag == "image":
            replace_with_html(img, clean_img_html)
        else:
            img.attrs["src"] = stream_url
            img.attrs["class"] = "epub-image"
            img.attrs["loading"] = item_loading
            img.attrs["alt"] = "Illustration"
            for attr_name in list((img.attributes or {}).keys()):
                lower = str(attr_name).lower()
                if lower in ("xlink:href", "href", "srcset", "viewbox", "preserveaspectratio") or "href" in lower:
                    try:
                        del img.attrs[attr_name]
                    except Exception:
                        pass

            anchor_parent = find_parent_tag(img, "a")
            if anchor_parent and anchor_parent.parent:
                a_href = (anchor_parent.attributes.get("href") or "").lower()
                if any(a_href.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")):
                    safe_unwrap(anchor_parent)

    return chapter_images


def intercept_scene_breaks(tree: HTMLParser) -> None:
    """Converts asterisks/ornament paragraphs to <s> scene breaks in memory."""
    for p in list(tree.css("p, div")):
        if p.parent is None or p.css_first("img, svg, picture"):
            continue
        text = p.text(strip=True)
        if not text or len(text) > 30:
            continue
        if detect_strict_scene_break(text, allow_breaks_flag=True):
            replace_with_html(p, f"<s>{html.escape(text)}</s>")


# ---------------------------------------------------------------------------
# 3-Tier Hybrid Memory Model Implementation
# ---------------------------------------------------------------------------
class HybridMemoryManager:
    """
    Manages in-memory book state across three performance tiers:
    - Tier 1: Instant Peek of the requested chapter (< 0.2ms).
    - Tier 2: Background RAM text buffer for all chapters (~2ms, ~1MB RAM).
              Enables instant global search and instant cross-chapter footnote jumps.
    - Tier 3: On-demand media stream straight from EPUB container (zero disk footprint).
    """

    def __init__(self):
        self._lock = threading.RLock()
        # { doc_id: { "meta": dict, "chapters": { idx: str }, "status": str, "timestamp": float } }
        self._active_sessions: Dict[str, Dict[str, Any]] = {}

    def has_session(self, doc_id: str) -> bool:
        with self._lock:
            return doc_id in self._active_sessions

    def get_session(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._active_sessions.get(doc_id)
            if session:
                session["timestamp"] = time.time()
            return session

    def register_session(self, doc_id: str, meta: dict) -> Dict[str, Any]:
        with self._lock:
            session = {
                "doc_id": doc_id,
                "meta": meta,
                "chapters": {},        # { chapter_index: cleaned_html }
                "sentences": {},       # { chapter_index: [sentence_list] }
                "images": {},          # { image_id: raw_bytes } in RAM
                "raw_htmls": {},       # streamed decompressed chapter bytes (pre-normalize)
                "status": "ready",     # "ready" -> "buffering" -> "complete"
                "filled_event": threading.Event(),
                "timestamp": time.time(),
            }
            self._active_sessions[doc_id] = session
            return session

    def drop_session(self, doc_id: str) -> None:
        with self._lock:
            self._active_sessions.pop(doc_id, None)

    def get_cached_image(self, doc_id: str, image_id: str) -> Optional[bytes]:
        """Retrieves decompressed image bytes directly from RAM if buffered."""
        with self._lock:
            session = self._active_sessions.get(doc_id)
            if not session or "images" not in session:
                return None
            clean = urllib.parse.unquote(image_id).strip()
            data = session["images"].get(clean)
            if data is not None:
                return data
            matched_key = resolve_map_key(session["images"].keys(), clean)
            if matched_key is not None:
                return session["images"].get(matched_key)
        return None

    def cache_image(self, doc_id: str, image_id: str, data: bytes) -> None:
        """Caches decompressed image bytes in RAM (bounded to prevent runaway memory)."""
        with self._lock:
            session = self._active_sessions.get(doc_id)
            if not session:
                session = self.register_session(doc_id, {})
            if "images" not in session:
                session["images"] = {}
            if len(session["images"]) >= 100:
                first_k = next(iter(session["images"]))
                session["images"].pop(first_k, None)
            clean = urllib.parse.unquote(image_id).strip()
            session["images"][clean] = data

    # --- Tier 1: Instant Active Chapter Peek ---
    def get_or_intercept_chapter(
        self,
        doc_id: str,
        chapter_idx: int,
        raw_bytes: bytes,
        meta: dict,
    ) -> str:
        """
        Tier 1: Returns chapter HTML from RAM immediately if buffered;
        otherwise runs in-memory normalization pipeline and caches in RAM (< 0.2ms).
        Also preloads any images referenced in this chapter directly into RAM.
        """
        session = self.get_session(doc_id) or self.register_session(doc_id, meta)

        with self._lock:
            cached_html = session["chapters"].get(chapter_idx)
            if cached_html is not None:
                return cached_html

        # Chapter not yet in RAM; normalize on-the-fly
        spine = meta.get("spine", [])
        chapter_href = spine[chapter_idx] if 0 <= chapter_idx < len(spine) else f"chapter_{chapter_idx}.xhtml"
        with self._lock:
            raw_htmls = session.get("raw_htmls") or {}
        cleaned_html, chapter_images = self._run_chapter_pipeline(
            raw_bytes,
            chapter_href,
            meta,
            next_has_header=_spine_next_has_header(spine, chapter_idx, raw_htmls),
        )

        with self._lock:
            session["chapters"][chapter_idx] = cleaned_html

        if "chapter_images" not in meta:
            meta["chapter_images"] = {}
        if str(chapter_idx) not in meta["chapter_images"]:
            meta["chapter_images"][str(chapter_idx)] = [img_id for img_id, _ in chapter_images]

        # Preload this chapter's images into RAM
        epub_path = meta.get("source_path")
        if epub_path and chapter_images:
            for img_id, zip_path in chapter_images:
                if not self.get_cached_image(doc_id, img_id):
                    try:
                        img_bytes = call_peeker_single(epub_path, zip_path)
                        self.cache_image(doc_id, img_id, img_bytes)
                    except Exception as e:
                        print(f"[RAM Image Cache] Failed preloading {img_id}: {e}")

        return cleaned_html

    def get_raw_chapter_bytes(self, doc_id: str, meta: dict, chapter_idx: int) -> Optional[bytes]:
        """Return streamed raw chapter bytes if Tier 2 already decompressed the book."""
        session = self.get_session(doc_id)
        if not session:
            return None
        spine = meta.get("spine", []) if meta else session.get("meta", {}).get("spine", [])
        if not (0 <= chapter_idx < len(spine)):
            return None
        with self._lock:
            raw_htmls = session.get("raw_htmls") or {}
        return _raw_for_href(raw_htmls, spine[chapter_idx])

    def wait_for_text_buffer(self, doc_id: str, timeout: float = 120.0) -> bool:
        """Block until the background RAM buffer finishes or `timeout` elapses."""
        session = self.get_session(doc_id)
        if not session:
            return False
        with self._lock:
            status = session.get("status")
            event = session.get("filled_event")
            spine_len = len((session.get("meta") or {}).get("spine") or [])
            buffered = len(session.get("chapters") or {})
        if status == "complete" or (spine_len and buffered >= spine_len):
            return True
        if event is None:
            return False
        return bool(event.wait(timeout=timeout))

    def get_buffer_status(self, doc_id: str) -> Dict[str, Any]:
        """Tier 2 RAM fill progress. Progress math must wait until ready=True."""
        session = self.get_session(doc_id)
        if not session:
            return {"status": "missing", "buffered": 0, "total": 0, "ready": False}
        with self._lock:
            status = session.get("status") or "ready"
            buffered = len(session.get("chapters") or {})
            spine_len = len((session.get("meta") or {}).get("spine") or [])
        ready = status == "complete" or (spine_len > 0 and buffered >= spine_len)
        return {
            "status": "complete" if ready else status,
            "buffered": buffered,
            "total": spine_len,
            "ready": ready,
        }

    # --- Tier 2: Background Text Buffer Preloader ---
    def start_background_text_buffer(
        self,
        doc_id: str,
        meta: dict,
        peeker_caller_fn: Any,
    ) -> None:
        """
        Tier 2: Spawns background worker to buffer all remaining text chapters
        into RAM (~2ms for C++ peek, ~15ms for Selectolax normalization).
        Navigation must not wait on this worker: TOC/page jumps peek one chapter.
        """
        session = self.get_session(doc_id) or self.register_session(doc_id, meta)

        with self._lock:
            if session["status"] in ("buffering", "complete"):
                return
            session["status"] = "buffering"
            event = session.get("filled_event")
            if event is None:
                event = threading.Event()
                session["filled_event"] = event
            else:
                event.clear()

        def worker():
            epub_path = meta.get("source_path")
            spine = meta.get("spine", [])
            event = session.get("filled_event")

            try:
                if not source_epub_is_present(epub_path):
                    with self._lock:
                        session["status"] = "missing"
                    print(f"[Tier 2] Book missing from path: {epub_path}")
                    return
                # Call C++ peeker for all HTML chapters in one shot
                all_raw_htmls = peeker_caller_fn(epub_path, mode="html")
                with self._lock:
                    session["raw_htmls"] = all_raw_htmls

                for idx, href in enumerate(spine):
                    with self._lock:
                        if idx in session["chapters"]:
                            continue

                    raw_bytes = _raw_for_href(all_raw_htmls, href)
                    if raw_bytes:
                        page_html, _ = self._run_chapter_pipeline(
                            raw_bytes,
                            href,
                            meta,
                            next_has_header=_spine_next_has_header(spine, idx, all_raw_htmls),
                        )
                        with self._lock:
                            session["chapters"][idx] = page_html

                with self._lock:
                    session["status"] = "complete"

            except Exception as e:
                with self._lock:
                    session["status"] = "error"
                print(f"[Tier 2 Error] Failed to buffer text chapters into RAM: {e}")
            finally:
                if event is not None:
                    event.set()

        threading.Thread(target=worker, daemon=True, name=f"buffer-{doc_id[:8]}").start()

    # --- In-Memory Chapter Pipeline ---
    def _run_chapter_pipeline(
        self,
        raw_bytes: bytes,
        chapter_href: str,
        meta: dict,
        next_has_header: bool = False,
    ) -> Tuple[str, List[Tuple[str, str]]]:
        """Applies normalization pipeline to raw chapter bytes completely in memory."""
        raw_str = raw_bytes.decode("utf-8", errors="ignore")
        try:
            raw_str = pre_parse_clean(raw_str)
        except Exception:
            pass

        tree = HTMLParser(raw_str)
        known_toc_titles, rich_toc_map = ensure_toc_normalizer_inputs(meta)

        try:
            standardize_footnotes(tree)
        except Exception:
            pass

        try:
            stamp_footnotes_from_map(tree, chapter_href, meta)
        except Exception:
            pass

        try:
            force_formatting_markers(tree, chapter_href)
        except Exception:
            pass

        try:
            _normalize_epub_html(
                tree=tree,
                known_toc_titles=known_toc_titles,
                current_href=chapter_href,
                rich_toc_map=rich_toc_map,
                next_has_header=next_has_header,
            )
        except Exception:
            pass

        # Image URL redirection, tag cleaning, eager/lazy loading
        chapter_images = intercept_image_tags(tree, chapter_href, meta)

        intercept_scene_breaks(tree)

        assign_epub_block_ids(tree)

        # 6. Target HTML extraction & Marker restoration
        target = tree.body or tree.root
        page_html = (target.html if target else tree.html) or ""
        page_html = re.sub(r">\s*\n+\s*<", "><", page_html)
        page_html = restore_inline_markers(page_html)

        return page_html, chapter_images

    # --- Fast In-Memory Global Search (Powered by Tier 2 Buffer) ---
    def search_in_memory(
        self,
        doc_id: str,
        query: str,
        match_case: bool = False,
        whole_word: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search buffered chapter HTML using the same query rules as library.search_book."""
        session = self.get_session(doc_id)
        if not session or not query.strip():
            return []

        q_norm = (
            query.replace("‘", "'")
            .replace("’", "'")
            .replace("´", "'")
            .replace("`", "'")
            .replace("“", '"')
            .replace("”", '"')
        )
        flags = 0 if match_case else re.IGNORECASE
        escaped_q = escape_search_query(q_norm)
        if not escaped_q:
            return []
        pattern_str = rf"\b{escaped_q}\b" if whole_word else escaped_q
        try:
            pattern = re.compile(pattern_str, flags)
        except Exception:
            return []

        with self._lock:
            chapters = list(session["chapters"].items())

        results: List[Dict[str, Any]] = []
        for ch_idx, ch_html in chapters:
            page_text = html_to_search_text(ch_html)
            for match in pattern.finditer(page_text):
                pos = match.start()
                context_start = max(0, pos - 50)
                context_end = min(len(page_text), match.end() + 50)
                snippet = page_text[context_start:context_end].strip()
                if context_start > 0:
                    snippet = "..." + snippet
                if context_end < len(page_text):
                    snippet = snippet + "..."
                results.append({
                    "page_index": ch_idx,
                    "snippet": snippet,
                    "position": pos,
                })

        return results

    # --- Cross-Chapter Footnote Resolver (Powered by Tier 2 Buffer) ---
    def resolve_footnote(self, doc_id: str, target_file: str, target_anchor: str) -> Optional[str]:
        """Looks up footnote definition from metadata map, then RAM chapter HTML."""
        session = self.get_session(doc_id)
        if not session:
            return None

        meta = session.get("meta", {})
        footnote_map = meta.get("footnote_map", {})
        clean_anchor = target_anchor.lstrip("#")
        rec = lookup_footnote_record(footnote_map, target_file, clean_anchor)
        if rec and rec.get("html"):
            return rec["html"]

        spine = meta.get("spine", [])
        target_page_idx = rec.get("page_index", -1) if rec else -1
        if target_page_idx is None or target_page_idx < 0:
            target_page_idx = _spine_page_index(spine, target_file)

        if target_page_idx < 0:
            return None

        with self._lock:
            page_html = session["chapters"].get(target_page_idx)

        if not page_html:
            return None

        tree = HTMLParser(page_html)
        jump_id = (rec.get("anchor_id") if rec else None) or clean_anchor
        el = (
            tree.css_first(f'[data-orig-id="{jump_id}"]')
            or tree.css_first(f'[id="{jump_id}"]')
            or tree.css_first(f'[name="{jump_id}"]')
        )
        if el:
            container = find_parent_tag(el, ("aside", "li", "p", "div")) or el.parent
            return (container or el).html

        return None


# Global Hybrid Memory Manager Singleton
memory_manager = HybridMemoryManager()


# ---------------------------------------------------------------------------
# Peek sidecars: userdata/library/<doc_id>/info.json + progress.json
# PDF / legacy sidecars: userdata/metadata/<doc_id>/info.json + progress.json
# ---------------------------------------------------------------------------


def book_sidecar_dir(doc_id: str) -> Path:
    return library_dir / str(doc_id)


def book_info_path(doc_id: str) -> Path:
    return book_sidecar_dir(doc_id) / "info.json"


def book_progress_path(doc_id: str) -> Path:
    return book_sidecar_dir(doc_id) / "progress.json"


def pdf_sidecar_dir(doc_id: str) -> Path:
    return metadata_dir / str(doc_id)


def pdf_info_path(doc_id: str) -> Path:
    return pdf_sidecar_dir(doc_id) / "info.json"


def pdf_progress_path(doc_id: str) -> Path:
    return pdf_sidecar_dir(doc_id) / "progress.json"


def delete_peek_sidecar(doc_id: str) -> None:
    """Remove userdata/library/<id>/ (info + progress) and drop the RAM session."""
    sidecar = book_sidecar_dir(doc_id)
    if sidecar.exists():
        shutil.rmtree(sidecar, ignore_errors=True)
    leftover_meta = metadata_dir / f"{doc_id}.json"
    if leftover_meta.exists():
        leftover_meta.unlink(missing_ok=True)
    memory_manager.drop_session(doc_id)


def delete_pdf_sidecar(doc_id: str) -> None:
    """Remove userdata/metadata/<id>/ (PDF/legacy info + progress). No shelf_status."""
    sidecar = pdf_sidecar_dir(doc_id)
    if sidecar.exists() and sidecar.is_dir():
        shutil.rmtree(sidecar, ignore_errors=True)


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_book_info(doc_id: str) -> Optional[Dict[str, Any]]:
    return _read_json_file(book_info_path(doc_id))


def save_book_info(doc_id: str, info: Dict[str, Any]) -> None:
    path = book_info_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_save_json(path, info, indent=2)


def load_book_progress(doc_id: str) -> Optional[Dict[str, Any]]:
    return _read_json_file(book_progress_path(doc_id))


def empty_book_progress(doc_id: str) -> Dict[str, Any]:
    return {
        "id": doc_id,
        "currentPage": 0,
        "lastSentenceId": None,
        "lastSentenceIndex": 0,
        "disable_br": False,
        "current_page": 0,
        "total_pages": 1,
        "progress_percent": 0,
    }


def _progress_int(src: Dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        if src.get(key) is None:
            continue
        try:
            return int(src.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return default


def normalize_shelf_status(value: Any) -> Optional[str]:
    """KOReader-style shelf: only hold/finished are stored. reading is the idle default."""
    raw = str(value or "").strip().lower().replace("_", " ")
    if raw in ("hold", "on hold"):
        return "hold"
    if raw in ("finished", "finish"):
        return "finished"
    return None


def sidecar_kind(doc_id: str) -> Optional[str]:
    if is_peek_book(doc_id):
        return "peek"
    if is_pdf_sidecar_book(doc_id):
        return "pdf"
    return None


def progress_file_for(doc_id: str, kind: Optional[str] = None) -> Path:
    resolved = kind or sidecar_kind(doc_id) or "peek"
    if resolved == "pdf":
        return pdf_progress_path(doc_id)
    return book_progress_path(doc_id)


def normalize_book_progress(
    doc_id: str,
    entry: Optional[Dict[str, Any]] = None,
    progress_path: Optional[Path] = None,
    allow_shelf: bool = True,
) -> Dict[str, Any]:
    src = entry or {}
    path = progress_path or book_progress_path(doc_id)
    prev = _read_json_file(path) or {}
    disable_br = src.get("disable_br")
    if disable_br is None:
        disable_br = prev.get("disable_br", False)
        if src.get("current_page") is None and prev.get("current_page") is not None:
            src = {**prev, **src}
    display_total = _progress_int(src, "total_pages", "totalPages", default=0)
    payload = {
        "id": doc_id,
        "currentPage": _progress_int(src, "currentPage"),
        "lastSentenceId": src.get("lastSentenceId"),
        "lastSentenceIndex": _progress_int(src, "lastSentenceIndex"),
        "disable_br": bool(disable_br),
        "current_page": _progress_int(src, "current_page", default=0),
        "total_pages": display_total if display_total > 0 else 1,
        "progress_percent": _progress_int(src, "progress_percent", default=0),
    }
    # Peek EPUB only. PDF/legacy metadata folders never persist shelf_status.
    if allow_shelf:
        if "shelf_status" in src:
            shelf = normalize_shelf_status(src.get("shelf_status"))
        else:
            shelf = normalize_shelf_status(prev.get("shelf_status"))
        if shelf:
            payload["shelf_status"] = shelf
    return payload


def save_book_progress(
    doc_id: str,
    entry: Dict[str, Any],
    rotate_old: bool = False,
    kind: Optional[str] = None,
) -> None:
    resolved = kind or sidecar_kind(doc_id) or "peek"
    path = progress_file_for(doc_id, resolved)
    allow_shelf = resolved == "peek"
    payload = normalize_book_progress(
        doc_id,
        entry,
        progress_path=path,
        allow_shelf=allow_shelf,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if rotate_old:
        save_json_rotate_old(path, payload, indent=2)
    else:
        safe_save_json(path, payload, indent=2)


def set_book_shelf_status(doc_id: str, status: str) -> Dict[str, Any]:
    """Set KOReader shelf status. reading clears the key; hold/finished are stored."""
    entry = dict(load_book_progress(doc_id) or empty_book_progress(doc_id))
    entry["shelf_status"] = status
    save_book_progress(doc_id, entry)
    item = peek_catalog_item(doc_id)
    if not item:
        raise FileNotFoundError(doc_id)
    return item


def is_peek_book(doc_id: str) -> bool:
    return book_info_path(doc_id).exists()


def is_pdf_sidecar_book(doc_id: str) -> bool:
    return pdf_info_path(doc_id).exists()


def load_pdf_info(doc_id: str) -> Optional[Dict[str, Any]]:
    return _read_json_file(pdf_info_path(doc_id))


def save_pdf_info(doc_id: str, info: Dict[str, Any]) -> None:
    path = pdf_info_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_save_json(path, info, indent=2)


def load_pdf_progress(doc_id: str) -> Optional[Dict[str, Any]]:
    return _read_json_file(pdf_progress_path(doc_id))


def load_any_progress(doc_id: str) -> Optional[Dict[str, Any]]:
    if is_peek_book(doc_id):
        return load_book_progress(doc_id)
    if is_pdf_sidecar_book(doc_id):
        return load_pdf_progress(doc_id)
    return None


def get_peek_source_path(doc_id: str) -> Optional[str]:
    info = load_book_info(doc_id)
    if not info:
        return None
    return info.get("source_path") or info.get("path")


def find_peek_id_by_path(epub_path: str) -> Optional[str]:
    target = str(Path(epub_path).resolve()) if epub_path else ""
    if not target or not library_dir.exists():
        return None
    target_l = target.lower()
    try:
        for folder in library_dir.iterdir():
            if not folder.is_dir():
                continue
            info = _read_json_file(folder / "info.json")
            if not info:
                continue
            stored = str(info.get("source_path") or info.get("path") or "")
            if stored and stored.lower() == target_l:
                return folder.name
    except Exception:
        return None
    return None


def overlay_catalog_item(row: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = str(row.get("id") or "")
    if not doc_id:
        return row
    info = load_book_info(doc_id)
    if not info:
        return row
    progress = load_book_progress(doc_id) or empty_book_progress(doc_id)
    merged = dict(row)
    merged["id"] = doc_id
    merged["fileName"] = row.get("fileName") or info.get("fileName")
    merged["title"] = row.get("title") or info.get("title")
    merged["source_path"] = info.get("source_path") or info.get("path")
    merged["path"] = info.get("path") or info.get("source_path")
    merged["is_peek"] = True
    merged["bookType"] = "epub"
    if info.get("language"):
        merged["language"] = info.get("language")
    # currentPage is the spine/HTML index used to resume the chapter peek.
    # current_page / total_pages are EPUB display pages (1024 chars), never spine length.
    merged["currentPage"] = progress.get("currentPage", 0)
    display_total = progress.get("total_pages")
    try:
        display_total = int(display_total) if display_total is not None else 0
    except (TypeError, ValueError):
        display_total = 0
    merged["totalPages"] = display_total if display_total > 1 else 1
    merged["total_pages"] = merged["totalPages"]
    if progress.get("current_page") is not None:
        merged["current_page"] = progress.get("current_page")
    if progress.get("progress_percent") is not None:
        merged["progress_percent"] = progress.get("progress_percent")
    merged["lastSentenceId"] = progress.get("lastSentenceId")
    merged["lastSentenceIndex"] = progress.get("lastSentenceIndex", 0)
    merged["disable_br"] = bool(progress.get("disable_br"))
    merged["shelf_status"] = normalize_shelf_status(progress.get("shelf_status")) or "reading"
    prog_file = book_progress_path(doc_id)
    try:
        merged["lastAccessed"] = prog_file.stat().st_mtime * 1000 if prog_file.exists() else 0
    except OSError:
        merged["lastAccessed"] = 0
    return merged


def peek_catalog_item(doc_id: str) -> Optional[Dict[str, Any]]:
    info = load_book_info(doc_id)
    if not info:
        return None
    return overlay_catalog_item({
        "id": doc_id,
        "fileName": info.get("fileName") or info.get("title"),
        "title": info.get("title") or info.get("fileName"),
    })


def list_peek_catalog() -> List[Dict[str, Any]]:
    if not library_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        for folder in library_dir.iterdir():
            if not folder.is_dir():
                continue
            item = peek_catalog_item(folder.name)
            if item:
                items.append(item)
    except Exception:
        return items
    return items


def _normalize_legacy_book_type(value: Any) -> Optional[str]:
    kind = str(value or "").strip().lower()
    if kind in ("pdf", "epub"):
        return kind
    return None


def split_pdf_sidecar_row(row: Dict[str, Any], doc_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """info.json = identity; progress.json = same schema as peek (no shelf_status)."""
    total_pages = _progress_int(row, "totalPages", "total_pages", default=1)
    info = {
        "id": doc_id,
        "fileName": row.get("fileName") or row.get("title") or doc_id,
        "title": row.get("title") or row.get("fileName") or doc_id,
        "bookType": _normalize_legacy_book_type(row.get("bookType")) or "pdf",
        "totalPages": total_pages if total_pages > 0 else 1,
    }
    if row.get("language"):
        info["language"] = row.get("language")
    progress = {
        "id": doc_id,
        "currentPage": row.get("currentPage"),
        "lastSentenceId": row.get("lastSentenceId"),
        "lastSentenceIndex": row.get("lastSentenceIndex"),
        "disable_br": row.get("disable_br"),
        "current_page": row.get("current_page"),
        "total_pages": row.get("total_pages") if row.get("total_pages") is not None else total_pages,
        "progress_percent": row.get("progress_percent"),
    }
    return info, progress


def upsert_pdf_sidecar(row: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = str(row.get("id") or "")
    if not doc_id:
        raise ValueError("missing book id")
    existing = load_pdf_info(doc_id) or {}
    merged_row = {**existing, **row, "id": doc_id}
    info, progress = split_pdf_sidecar_row(merged_row, doc_id)
    save_pdf_info(doc_id, info)
    save_book_progress(doc_id, progress, rotate_old=False, kind="pdf")
    return pdf_catalog_item(doc_id) or overlay_pdf_catalog_item(info)


def overlay_pdf_catalog_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """PDF/legacy catalog row. Never exposes shelf_status."""
    doc_id = str(row.get("id") or "")
    if not doc_id:
        return row
    info = load_pdf_info(doc_id)
    if not info:
        return row
    progress = load_pdf_progress(doc_id) or empty_book_progress(doc_id)
    merged: Dict[str, Any] = {
        "id": doc_id,
        "fileName": info.get("fileName") or row.get("fileName") or info.get("title"),
        "title": info.get("title") or info.get("fileName") or row.get("title"),
        "bookType": info.get("bookType") or row.get("bookType") or "pdf",
        "is_peek": False,
    }
    if info.get("language"):
        merged["language"] = info.get("language")
    info_total = _progress_int(info, "totalPages", "total_pages", default=0)
    prog_total = _progress_int(progress, "total_pages", "totalPages", default=0)
    merged["totalPages"] = info_total or prog_total or 1
    merged["total_pages"] = prog_total or merged["totalPages"]
    merged["currentPage"] = progress.get("currentPage", 0)
    if progress.get("current_page") is not None:
        merged["current_page"] = progress.get("current_page")
    if progress.get("progress_percent") is not None:
        merged["progress_percent"] = progress.get("progress_percent")
    merged["lastSentenceId"] = progress.get("lastSentenceId")
    merged["lastSentenceIndex"] = progress.get("lastSentenceIndex", 0)
    merged["disable_br"] = bool(progress.get("disable_br"))
    prog_file = pdf_progress_path(doc_id)
    try:
        merged["lastAccessed"] = prog_file.stat().st_mtime * 1000 if prog_file.exists() else 0
    except OSError:
        merged["lastAccessed"] = 0
    return merged


def pdf_catalog_item(doc_id: str) -> Optional[Dict[str, Any]]:
    if is_peek_book(doc_id):
        return None
    info = load_pdf_info(doc_id)
    if not info:
        return None
    return overlay_pdf_catalog_item({"id": doc_id, "fileName": info.get("fileName")})


def list_pdf_catalog() -> List[Dict[str, Any]]:
    if not metadata_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        for folder in metadata_dir.iterdir():
            if not folder.is_dir():
                continue
            item = pdf_catalog_item(folder.name)
            if item:
                items.append(item)
    except Exception:
        return items
    return items


def catalog_item_for(doc_id: str) -> Optional[Dict[str, Any]]:
    return peek_catalog_item(doc_id) or pdf_catalog_item(doc_id)


def _load_catalog() -> List[Dict[str, Any]]:
    if not library_file.exists():
        return []
    try:
        with open(library_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_catalog(books: List[Dict[str, Any]]) -> None:
    safe_save_json(library_file, books, indent=2)


def migrate_peek_sidecars() -> None:
    """Move leftover metadata/*.json files into userdata/library/<id>/info.json.

    Remaining library.json PDF/legacy rows are handled by migrate_pdf_sidecars(),
    which deletes library.json after every row has a sidecar.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    books = _load_catalog()
    by_id = {str(row.get("id")): row for row in books if row.get("id")}

    if metadata_dir.exists():
        for meta_file in list(metadata_dir.glob("*.json")):
            doc_id = meta_file.stem
            dest = book_info_path(doc_id)
            if dest.exists():
                try:
                    meta_file.unlink()
                except OSError:
                    pass
                continue
            info = _read_json_file(meta_file)
            if not info:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                meta_file.replace(dest)
            except OSError:
                try:
                    safe_save_json(dest, info, indent=2)
                    meta_file.unlink()
                except OSError as exc:
                    print(f"[LIBRARY] Failed moving metadata for {doc_id}: {exc}")
                    continue
            row = by_id.get(doc_id) or {}
            if not book_progress_path(doc_id).exists():
                save_book_progress(doc_id, row, rotate_old=False)

    for row in books:
        doc_id = str(row.get("id") or "")
        if not doc_id:
            continue
        if book_info_path(doc_id).exists() or row.get("source_path") or row.get("path"):
            if book_info_path(doc_id).exists() and not book_progress_path(doc_id).exists():
                save_book_progress(doc_id, row, rotate_old=False)


def retire_library_json() -> None:
    """Delete userdata/library.json once every catalog row has a sidecar folder."""
    if not library_file.exists():
        return
    books = _load_catalog()
    pending: List[Dict[str, Any]] = []
    for row in books:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("id") or "")
        if not doc_id:
            continue
        if is_peek_book(doc_id) or is_pdf_sidecar_book(doc_id):
            continue
        pending.append(row)
    if pending:
        print(f"[LIBRARY] Keeping library.json: {len(pending)} row(s) failed sidecar migrate")
        _save_catalog(pending)
        return
    try:
        library_file.unlink()
        print("[LIBRARY] Deleted userdata/library.json after sidecar migration")
    except OSError as exc:
        print(f"[LIBRARY] Failed deleting library.json: {exc}")


def migrate_pdf_sidecars() -> None:
    """Split leftover library.json PDF/legacy rows into metadata/<id>/info.json + progress.json."""
    metadata_dir.mkdir(parents=True, exist_ok=True)
    books = _load_catalog()
    for row in books:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("id") or "")
        if not doc_id or is_peek_book(doc_id):
            continue
        if row.get("source_path") or row.get("path"):
            continue
        try:
            if not is_pdf_sidecar_book(doc_id):
                upsert_pdf_sidecar(row)
            elif not pdf_progress_path(doc_id).exists():
                _, progress = split_pdf_sidecar_row(row, doc_id)
                save_book_progress(doc_id, progress, rotate_old=False, kind="pdf")
        except Exception as exc:
            print(f"[LIBRARY] Failed migrating PDF sidecar for {doc_id}: {exc}")
    retire_library_json()


def delete_library_book(doc_id: str) -> bool:
    """Remove peek sidecar, PDF sidecar, and extracted content under userdata/content/<id>/."""
    found = is_peek_book(doc_id) or is_pdf_sidecar_book(doc_id)
    book_dir = content_dir / doc_id
    if book_dir.exists():
        found = True
        shutil.rmtree(book_dir, ignore_errors=True)
    for ext in [".json", ".pdf", ".epub"]:
        file_path = content_dir / f"{doc_id}{ext}"
        if file_path.exists():
            found = True
            try:
                file_path.unlink()
            except Exception:
                pass
    delete_peek_sidecar(doc_id)
    delete_pdf_sidecar(doc_id)
    return found


# ---------------------------------------------------------------------------
# Master Manifest Interceptor ("Save Only What We Plan")
# ---------------------------------------------------------------------------
def parse_epub_toc(
    epub_path: Path | str,
    manifest: Dict[str, Any],
    htmls: Optional[Dict[str, bytes]] = None,
) -> List[Dict[str, Any]]:
    """
    Parses Table of Contents from EPUB2 NCX (toc.ncx) or EPUB3 HTML5 Nav (toc.xhtml / nav.xhtml).
    Maps chapter targets to page indices based on spine.
    """
    toc_file = manifest.get("toc_file", "")
    spine = manifest.get("spine", [])
    raw = ""

    if toc_file:
        if htmls and toc_file in htmls:
            raw = htmls[toc_file].decode("utf-8", errors="ignore")
        elif Path(epub_path).exists():
            try:
                raw = call_peeker_single(epub_path, toc_file).decode("utf-8", errors="ignore")
            except Exception:
                pass

    toc: List[Dict[str, Any]] = []

    # 1. Try EPUB2 NCX
    if toc_file and "ncx" in toc_file.lower() and raw:
        try:
            root = ET.fromstring(raw)
            ns = "{http://www.daisy.org/z3986/2005/ncx/}"

            def walk_ncx(elem, level=1):
                for np in elem.findall(f"{ns}navPoint"):
                    lbl = np.find(f"{ns}navLabel")
                    title = ""
                    if lbl is not None:
                        t_node = lbl.find(f"{ns}text")
                        if t_node is not None and t_node.text:
                            title = t_node.text.strip()
                    cnt = np.find(f"{ns}content")
                    src = cnt.attrib.get("src", "") if cnt is not None else ""
                    chref = src.split("#")[0]
                    anchor = src.split("#")[1] if "#" in src else None
                    pidx = -1
                    for idx, s in enumerate(spine):
                        if posixpath.basename(s).lower() == posixpath.basename(chref).lower():
                            pidx = idx
                            break
                    if title:
                        toc.append({"title": title, "level": level, "page_index": pidx, "anchor_id": anchor})
                    walk_ncx(np, level + 1)

            nav_map = root.find(f"{ns}navMap")
            if nav_map is not None:
                walk_ncx(nav_map)
        except Exception as e:
            print(f"[TOC] NCX parse error: {e}")

    # 2. Try EPUB3 HTML5 Nav if NCX yielded nothing or file is xhtml
    if not toc and raw and ("xhtml" in toc_file.lower() or "html" in toc_file.lower()):
        try:
            tree = HTMLParser(raw)
            nav = tree.css_first('nav[epub\\:type="toc"]') or tree.css_first("nav#toc") or tree.css_first("nav")
            if nav:
                for a in nav.css("ol li a, ul li a"):
                    title = a.text(strip=True)
                    src = a.attributes.get("href", "")
                    chref = src.split("#")[0]
                    anchor = src.split("#")[1] if "#" in src else None
                    pidx = -1
                    for idx, s in enumerate(spine):
                        if posixpath.basename(s).lower() == posixpath.basename(chref).lower():
                            pidx = idx
                            break
                    if title:
                        toc.append({"title": title, "level": 1, "page_index": pidx, "anchor_id": anchor})
        except Exception as e:
            print(f"[TOC] HTML parse error: {e}")

    return toc


def _spine_page_index(spine: Optional[List[str]], href_or_file: str) -> int:
    if not href_or_file:
        return -1
    base = posixpath.basename(str(href_or_file).split("#")[0].split("?")[0]).lower()
    if not base:
        return -1
    for idx, item in enumerate(spine or []):
        if posixpath.basename(item).lower() == base:
            return idx
    return -1


def _resolve_epub_href(current_href: str, href: str) -> Tuple[str, str]:
    """Resolve a possibly-relative EPUB href to (file_path, fragment)."""
    href = (href or "").strip()
    if not href:
        return current_href or "", ""
    if "#" in href:
        file_part, frag = href.split("#", 1)
    else:
        file_part, frag = href, ""
    file_part = file_part.split("?")[0]
    if not file_part:
        return current_href or "", frag
    if current_href:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(current_href), file_part))
        return joined.lstrip("/"), frag
    return file_part.lstrip("/"), frag


def _htmls_bytes_for(htmls: Dict[str, bytes], href: str) -> bytes:
    if href in htmls:
        return htmls[href]
    base = posixpath.basename(href).lower()
    for key, raw in htmls.items():
        if posixpath.basename(key).lower() == base:
            return raw
    return b""


def _inside_footnote_def(node: Optional[Node]) -> bool:
    cur = node
    while cur is not None:
        attrs = cur.attributes or {}
        epub_type = (attrs.get("epub:type") or "").lower()
        role = (attrs.get("role") or "").lower()
        if cur.tag in ("aside", "dd") or epub_type in ("footnote", "endnote") or role in ("doc-footnote", "doc-endnote"):
            return True
        cur = cur.parent
    return False


def _footnote_def_id(el: Optional[Node]) -> str:
    if el is None:
        return ""
    attrs = el.attributes or {}
    el_id = str(attrs.get("id") or attrs.get("name") or "").strip()
    epub_type = (attrs.get("epub:type") or "").lower()
    role = (attrs.get("role") or "").lower()
    classes = (attrs.get("class") or "").lower()
    if epub_type in ("footnote", "endnote") or role in ("doc-footnote", "doc-endnote"):
        if el_id:
            return el_id
        child = el.css_first("[id], [name]")
        if child:
            return str((child.attributes or {}).get("id") or (child.attributes or {}).get("name") or "")
        return ""
    lower_id = el_id.lower()
    if (
        lower_id.startswith("cite_note")
        or lower_id.startswith("tn")
        or lower_id.startswith("fn-def")
        or lower_id.startswith("fn_def")
        or "sdfootnote" in lower_id
    ):
        return el_id
    if "footnote" in classes or "endnote" in classes:
        return el_id
    parent = el.parent
    parent_class = ((parent.attributes or {}).get("class") or "").lower() if parent is not None else ""
    if el.tag == "li" and el_id and ("reference" in classes or "reference" in parent_class):
        return el_id
    if el.tag in ("p", "div", "li"):
        for anchor in el.css("a"):
            href = ((anchor.attributes or {}).get("href") or "").lower()
            if "sdfootnote" in href and "anc" in href:
                return (anchor.attributes or {}).get("href", "").split("#")[-1].lstrip("#") or el_id
    return ""


def _is_footnote_callout(anchor: Optional[Node]) -> bool:
    if anchor is None:
        return False
    href = (anchor.attributes or {}).get("href") or ""
    if "#" not in href or href.startswith(("http://", "https://", "mailto:")):
        return False
    if _inside_footnote_def(anchor):
        return False
    attrs = anchor.attributes or {}
    epub_type = (attrs.get("epub:type") or "").lower()
    role = (attrs.get("role") or "").lower()
    classes = set((attrs.get("class") or "").lower().split())
    parent = anchor.parent
    parent_class = ((parent.attributes or {}).get("class") or "").lower() if parent is not None else ""
    has_sup = find_parent_tag(anchor, "sup") is not None or anchor.css_first("sup") is not None
    return (
        epub_type == "noteref"
        or role == "doc-noteref"
        or bool(classes & {"noteref", "footnote-ref", "epub-noteref", "reference"})
        or has_sup
        or "reference" in parent_class
    )


def _footnote_twins(anchor_id: str) -> List[str]:
    if not anchor_id:
        return []
    twins = []
    if "anc" in anchor_id:
        twins.append(anchor_id.replace("anc", "sym"))
    if "sym" in anchor_id:
        twins.append(anchor_id.replace("sym", "anc"))
    return [item for item in twins if item and item != anchor_id]


def _put_footnote_keys(footnote_map: Dict[str, Any], rec: Dict[str, Any], *keys: str) -> None:
    for key in keys:
        if key:
            footnote_map[str(key)] = rec


def lookup_footnote_record(
    footnote_map: Optional[Dict[str, Any]],
    file_part: str = "",
    frag: str = "",
    extra_key: str = "",
) -> Optional[Dict[str, Any]]:
    """Resolve a footnote_map entry to a TOC-shaped record (or wrap a legacy string)."""
    if not footnote_map or not (frag or extra_key):
        return None
    base = posixpath.basename((file_part or "").split("?")[0]) if file_part else ""
    candidates = [
        extra_key,
        f"{base}#{frag}" if base and frag else "",
        f"{file_part}#{frag}" if file_part and frag else "",
        frag,
    ]
    rec = None
    for key in candidates:
        if key and key in footnote_map:
            rec = footnote_map[key]
            break
    if rec is None:
        lower_frag = (frag or "").lower()
        lower_base = base.lower()
        lower_extra = (extra_key or "").lower()
        for key, value in footnote_map.items():
            k_lower = str(key).lower()
            if lower_extra and k_lower == lower_extra:
                rec = value
                break
            if lower_base and lower_frag and k_lower == f"{lower_base}#{lower_frag}":
                rec = value
                break
            if lower_frag and k_lower == lower_frag:
                rec = value
                break
    if rec is None:
        return None
    if isinstance(rec, str):
        return {
            "html": rec,
            "page_index": -1,
            "file": base,
            "anchor_id": frag,
        }
    if isinstance(rec, dict):
        return rec
    return None


def footnote_map_needs_rebuild(footnote_map: Any) -> bool:
    """True when footnote_map is missing, not a dict, or still old string values.
    An empty dict is valid (book has no footnotes).
    """
    if footnote_map is None:
        return True
    if not isinstance(footnote_map, dict):
        return True
    if not footnote_map:
        return False
    first = next(iter(footnote_map.values()), None)
    return not isinstance(first, dict)


def meta_maps_need_rebuild(meta: Optional[Dict[str, Any]]) -> bool:
    """Rebuild only when map keys are missing or the stored types are stale."""
    if not meta:
        return True
    if not meta.get("reader_pages_filtered"):
        return True
    if "chapter_images" not in meta or not isinstance(meta.get("chapter_images"), dict):
        return True
    if "toc_map" not in meta or not isinstance(meta.get("toc_map"), list):
        return True
    if "footnote_map" not in meta:
        return True
    return footnote_map_needs_rebuild(meta.get("footnote_map"))


def stamp_footnotes_from_map(tree: HTMLParser, chapter_href: str, meta: dict) -> None:
    """Mark callouts/definitions from the book-wide footnote_map so the UI can attach clicks."""
    footnote_map = meta.get("footnote_map") or {}
    if not footnote_map:
        return

    for el in list(tree.css("aside, li, p, div, section, dd, [id], [name]")):
        attrs = el.attributes or {}
        el_id = str(attrs.get("id") or attrs.get("name") or "")
        rec = lookup_footnote_record(footnote_map, chapter_href, el_id) if el_id else None
        if rec and rec.get("anchor_id") == el_id:
            el.attrs["epub:type"] = "footnote"
            continue
        # Same-page callouts point at the definition id. Only stamp the
        # definition wrapper (backlink to the callout), never the story <p>.
        for anchor in el.css("a"):
            href = (anchor.attributes or {}).get("href") or ""
            if "#" not in href:
                continue
            file_part, frag = _resolve_epub_href(chapter_href, href)
            rec = lookup_footnote_record(footnote_map, file_part, frag)
            if not rec:
                continue
            def_id = str(rec.get("anchor_id") or "")
            callout_id = str(rec.get("callout_id") or "")
            on_def_page = posixpath.basename(chapter_href).lower() == str(rec.get("file") or "").lower()
            points_at_def = frag == def_id or frag in _footnote_twins(def_id)
            points_at_callout = bool(callout_id) and (
                frag == callout_id or frag in _footnote_twins(callout_id)
            )
            if not on_def_page or points_at_def or not points_at_callout:
                continue
            if _is_footnote_callout(anchor):
                continue
            if not el_id and def_id:
                el.attrs["id"] = def_id
                el_id = def_id
            el.attrs["epub:type"] = "footnote"
            break

    for anchor in tree.css("a"):
        href = (anchor.attributes or {}).get("href") or ""
        if "#" not in href or href.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, frag = _resolve_epub_href(chapter_href, href)
        rec = lookup_footnote_record(footnote_map, file_part, frag)
        if not rec:
            continue
        if _inside_footnote_def(anchor):
            anchor.attrs["epub:type"] = "backlink"
        else:
            anchor.attrs["epub:type"] = "noteref"


def extract_epub_footnotes(
    htmls: Dict[str, bytes],
    spine: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Cross-file footnote index shaped like toc_map.
    Values are { html, page_index, file, anchor_id, callout_page_index?, callout_id? }.
    Keys include fragment, basename#frag, and sdfootnote anc/sym twins.
    """
    spine = list(spine or [])
    if not spine:
        spine = list(htmls.keys())

    footnote_map: Dict[str, Any] = {}
    chapters: List[Tuple[int, str, HTMLParser]] = []

    for idx, href in enumerate(spine):
        raw = _htmls_bytes_for(htmls, href)
        if not raw:
            continue
        tree = HTMLParser(raw.decode("utf-8", errors="ignore"))
        chapters.append((idx, href, tree))

    def put_record(rec: Dict[str, Any], *extra_ids: str) -> None:
        file_name = rec.get("file") or ""
        anchor_id = rec.get("anchor_id") or ""
        keys = [
            anchor_id,
            f"{file_name}#{anchor_id}" if file_name and anchor_id else "",
        ]
        for extra in extra_ids:
            if extra:
                keys.extend([extra, f"{file_name}#{extra}" if file_name else extra])
                for twin in _footnote_twins(extra):
                    keys.extend([twin, f"{file_name}#{twin}" if file_name else twin])
        for twin in _footnote_twins(anchor_id):
            keys.extend([twin, f"{file_name}#{twin}" if file_name else twin])
        _put_footnote_keys(footnote_map, rec, *keys)

    for idx, href, tree in chapters:
        ch_base = posixpath.basename(href)
        for el in tree.css("aside, li, p, div, section, dd"):
            def_id = _footnote_def_id(el)
            if not def_id:
                continue
            container = el
            rec = {
                "html": container.html or "",
                "page_index": idx,
                "file": ch_base,
                "anchor_id": def_id,
            }
            extra_ids: List[str] = []
            for back in container.css("a"):
                bhref = (back.attributes or {}).get("href") or ""
                if "#" not in bhref or bhref.startswith(("http://", "https://", "mailto:")):
                    continue
                cfile, cfrag = _resolve_epub_href(href, bhref)
                if not cfrag:
                    continue
                if cfrag == def_id or cfrag in _footnote_twins(def_id):
                    continue
                extra_ids.append(cfrag)
                rec["callout_id"] = rec.get("callout_id") or cfrag
                cidx = _spine_page_index(spine, cfile or href)
                if rec.get("callout_page_index") is None and cidx >= 0:
                    rec["callout_page_index"] = cidx
            put_record(rec, *extra_ids)

    for idx, href, tree in chapters:
        ch_base = posixpath.basename(href)
        for anchor in tree.css("a"):
            if not _is_footnote_callout(anchor):
                continue
            a_href = (anchor.attributes or {}).get("href") or ""
            dfile, dfrag = _resolve_epub_href(href, a_href)
            rec = lookup_footnote_record(footnote_map, dfile, dfrag)
            if rec is None:
                continue
            callout_id = str((anchor.attributes or {}).get("id") or (anchor.attributes or {}).get("name") or "")
            sup = find_parent_tag(anchor, "sup")
            if sup is not None:
                callout_id = callout_id or str((sup.attributes or {}).get("id") or "")
            if rec.get("callout_page_index") is None:
                rec["callout_page_index"] = idx
            if not callout_id:
                twins = _footnote_twins(str(rec.get("anchor_id") or ""))
                callout_id = twins[0] if twins else dfrag
            if callout_id:
                rec["callout_id"] = rec.get("callout_id") or callout_id
                _put_footnote_keys(
                    footnote_map,
                    rec,
                    callout_id,
                    f"{ch_base}#{callout_id}",
                )

    return footnote_map


def intercept_manifest(
    cpp_manifest: Dict[str, Any],
    epub_path: Path,
    doc_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Intercepts the file inventory emitted by C++ peeker manifest.
    Builds lightweight metadata and writes ONLY userdata/metadata/<doc_id>.json.
    Zero image files and zero chapter files are extracted to disk.
    """
    doc_id = doc_id or str(uuid.uuid4())
    epub_path = Path(epub_path).resolve()

    spine = list(cpp_manifest.get("spine", []) or [])
    raw_images = cpp_manifest.get("images", [])

    # Build image map: clean_id -> zip_path
    image_map: Dict[str, str] = {}
    for img_path in raw_images:
        safe_name = unique_image_map_id(image_map, img_path)
        image_map[safe_name] = img_path

    # Stream HTML in ~2ms to map chapter images, TOC, and footnotes upfront
    htmls = {}
    if epub_path.exists() and spine:
        try:
            htmls = call_peeker_stream_html(epub_path)
        except Exception as e:
            print(f"[Peeker] HTML stream error during manifest interception: {e}")

    spine = filter_reader_spine(spine, htmls)

    # Map each chapter to images contained inside it
    IMG_SRC_REGEX = re.compile(
        r'<(?:img|image)\b[^>]*(?:src|href|xlink:href)=["\']([^"\']+)["\']',
        re.I,
    )
    chapter_images: Dict[str, List[str]] = {}

    for idx, href in enumerate(spine):
        raw = _htmls_bytes_for(htmls, href)
        if raw:
            text = raw.decode("utf-8", errors="ignore")
            matches = IMG_SRC_REGEX.findall(text)
            clean_matches = list(dict.fromkeys(
                posixpath.basename(m.split("#")[0].split("?")[0])
                for m in matches if m.strip()
            ))
            chapter_images[str(idx)] = clean_matches
        else:
            chapter_images[str(idx)] = []

    # Parse ToC and Footnotes against the filtered reader spine
    toc_manifest = dict(cpp_manifest)
    toc_manifest["spine"] = spine
    toc_map = parse_epub_toc(epub_path, toc_manifest, htmls)
    footnote_map = extract_epub_footnotes(htmls, spine)
    known_toc_titles, rich_toc_map = build_toc_normalizer_inputs(toc_map, spine)

    metadata = {
        "id": doc_id,
        "source_path": str(epub_path),
        "path": str(epub_path),
        "fileName": epub_path.stem,
        "title": cpp_manifest.get("title") or epub_path.stem,
        "language": cpp_manifest.get("language", "en"),
        "total_pages": len(spine),
        "totalPages": len(spine),
        "spine": spine,
        "image_map": image_map,
        "chapter_images": chapter_images,
        "toc_map": toc_map,
        "footnote_map": footnote_map,
        "known_toc_titles": sorted(known_toc_titles),
        "rich_toc_map": rich_toc_map,
        "css": list(cpp_manifest.get("css") or []),
        "reader_pages_filtered": True,
        "bookType": "epub",
        "is_peek": True,
        "created_at": int(time.time()),
    }

    # SAVE ONLY WHAT WE PLAN: userdata/library/<doc_id>/info.json (no content extract)
    save_book_info(doc_id, metadata)
    if not book_progress_path(doc_id).exists():
        save_book_progress(doc_id, empty_book_progress(doc_id), rotate_old=False)

    # Register in memory session (reuse the HTML stream already sitting in RAM)
    session = memory_manager.register_session(doc_id, metadata)
    if htmls:
        with memory_manager._lock:
            session["raw_htmls"] = htmls

    return metadata


def intercept_chapter(
    raw_bytes: bytes,
    chapter_href: str,
    meta: dict,
    chapter_idx: int,
    doc_id: Optional[str] = None,
) -> str:
    """
    In-memory Selectolax normalization for a single chapter (peek.md §4).
    When doc_id is provided, caches the result in HybridMemoryManager (Tier 1).
    """
    if doc_id is not None:
        return memory_manager.get_or_intercept_chapter(
            doc_id=doc_id,
            chapter_idx=chapter_idx,
            raw_bytes=raw_bytes,
            meta=meta,
        )
    href = chapter_href or f"chapter_{chapter_idx}.xhtml"
    cleaned_html, _ = memory_manager._run_chapter_pipeline(raw_bytes, href, meta)
    return cleaned_html


def _image_size_from_bytes(data: Optional[bytes]) -> Tuple[int, int]:
    """Same header parse as library.get_image_size, from RAM bytes."""
    if not data:
        return 0, 0
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            w, h = struct.unpack(">LL", data[16:24])
            return int(w), int(h)
        if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if data.startswith(b"\xff\xd8"):
            i = 2
            while i < len(data):
                while i < len(data) and data[i] == 0xFF:
                    i += 1
                if i >= len(data):
                    break
                marker = data[i]
                i += 1
                if 0xC0 <= marker <= 0xC3 and i + 6 < len(data):
                    h = (data[i + 3] << 8) + data[i + 4]
                    w = (data[i + 5] << 8) + data[i + 6]
                    return int(w), int(h)
                if i + 1 >= len(data):
                    break
                length = (data[i] << 8) + data[i + 1]
                i += length
    except Exception:
        pass
    return 0, 0


def peek_classify_ornament(filename: str, data: Optional[bytes], count: int) -> Dict[str, Any]:
    """library.classify_ornament_image using peeked bytes instead of a disk file."""
    clues = re.split(r"[^a-zA-Z0-9]", str(filename).lower())
    shape = "●"
    if "circle" in clues:
        shape = "●"
    elif "box" in clues or "square" in clues:
        shape = "■"
    elif "star" in clues:
        shape = "★"
    elif "diamond" in clues or "orn" in clues:
        shape = "◆"
    elif "triangle" in clues:
        shape = "▼"

    kw_match = any(keyword in clues for keyword in ORNAMENT_KEYWORDS)
    w, h = _image_size_from_bytes(data)
    symbol_count = 1
    is_symbolic = False
    if kw_match:
        is_symbolic = True
        nums = re.findall(r"\d+", str(filename))
        if nums:
            symbol_count = int(nums[-1])
        elif h and h > 0:
            symbol_count = max(1, round(w / h))
    elif h and 0 < h < 150 and w < 1000:
        if w / h >= 1.5:
            is_symbolic = True
            symbol_count = max(1, round(w / h))
    if is_symbolic:
        symbol_count = min(15, max(1, symbol_count))
        return {"kind": "symbols", "text": "".join([shape] * symbol_count)}
    if w and h and w <= 150 and h <= 150 and count > 5:
        return {"kind": "wrap"}
    return {"kind": "skip"}


def load_master_css(epub_path: Optional[str], css_names: Optional[List[str]]) -> str:
    if not epub_path or not css_names:
        return ""
    parts: List[str] = []
    for name in css_names:
        try:
            raw = call_peeker_single(epub_path, name)
            parts.append(raw.decode("utf-8", errors="ignore"))
        except Exception:
            continue
    return "\n".join(parts)


def _peek_image_bytes(doc_id: str, img_id: str, zip_path: str, epub_path: Optional[str]) -> Optional[bytes]:
    cached = memory_manager.get_cached_image(doc_id, img_id)
    if cached:
        return cached
    if not epub_path or not zip_path:
        return None
    try:
        data = call_peeker_single(epub_path, zip_path)
        memory_manager.cache_image(doc_id, img_id, data)
        return data
    except Exception:
        return None


def peek_css_scene_break_inner(
    class_name: str,
    count: int,
    master_css: str,
    image_map: Dict[str, str],
    doc_id: str,
    epub_path: Optional[str],
) -> Optional[str]:
    """Same CSS ornament resolution as library.css_scene_break_inner, RAM images."""
    pattern = r"\." + re.escape(class_name) + r"\s*\{([^}]+)\}"
    matches = re.findall(pattern, master_css or "")
    for block in matches:
        block_lower = block.lower()
        if not any(keyword in block_lower for keyword in CSS_ORNAMENT_KEYWORDS):
            continue
        url = first_css_url(block)
        if url:
            matched = resolve_image_map_entry(image_map, url) or resolve_image_map_entry(
                image_map, posixpath.basename(url)
            )
            if matched:
                img_id, zip_path = matched
                data = _peek_image_bytes(doc_id, img_id, zip_path, epub_path)
                classified = peek_classify_ornament(img_id, data, count)
                if classified["kind"] == "symbols":
                    return classified["text"]
                src = f"/api/library/image/{doc_id}/{urllib.parse.quote(img_id)}"
                return epub_image_html(src, "eager")
            return "◆ ◆ ◆"
        return "◆ ◆ ◆"
    return None


def peek_process_css_scene_breaks(
    pages: List[str],
    master_css: str,
    image_map: Dict[str, str],
    doc_id: str,
    epub_path: Optional[str],
) -> List[str]:
    """library.process_css_scene_breaks: count empty classes across ALL pages, then convert."""
    if not pages:
        return pages
    class_counts: Dict[str, int] = {}
    for page_html in pages:
        tree = HTMLParser(page_html)
        for tag in tree.css("hr, div, p, span"):
            if not tag.text(strip=True) and not tag.css_first("img, image, svg"):
                classes = (tag.attributes or {}).get("data-orig-class", "").split()
                for class_name in classes:
                    class_counts[class_name] = class_counts.get(class_name, 0) + 1

    confirmed_classes: Dict[str, str] = {}
    if master_css:
        for class_name, count in class_counts.items():
            if count < 4:
                continue
            inner = peek_css_scene_break_inner(
                class_name, count, master_css, image_map, doc_id, epub_path
            )
            if inner is not None:
                confirmed_classes[class_name] = inner

    new_pages: List[str] = []
    for page_html in pages:
        if "<hr" not in page_html and "data-orig-class=" not in page_html:
            new_pages.append(page_html)
            continue
        tree = HTMLParser(page_html)
        modified = False
        blocks = nodes_in_document_order(
            tree,
            ("h1", "h2", "h3", "h4", "h5", "h6",
             "p", "div", "span", "hr", "img", "image", "svg"),
        )
        for i, tag in enumerate(blocks):
            if tag.tag not in ["hr", "div", "p", "span"]:
                continue
            if tag.text(strip=True) or tag.css_first("img, image, svg"):
                continue
            classes = (tag.attributes or {}).get("data-orig-class", "").split()
            inner = next((confirmed_classes[c] for c in classes if c in confirmed_classes), None)
            if inner is None or not sandwich_allows_scene_break(blocks, i):
                continue
            replace_with_html(tag, f"{scene_break_open_tag(tag)}{inner}</s>")
            modified = True
        if strip_scene_break_inner_wrappers(tree):
            modified = True
        if modified:
            target = tree.body or tree.root
            page_str = (target.html if target else tree.html) or ""
            page_str = re.sub(r">\s*\n+\s*<", "><", page_str)
        else:
            page_str = page_html
        page_str = re.sub(r'\s*data-orig-class="[^"]*"', "", page_str)
        page_str = re.sub(r"\s*data-orig-class='[^']*'", "", page_str)
        new_pages.append(page_str)
    return new_pages


def peek_process_image_scene_breaks(
    pages: List[str],
    image_map: Dict[str, str],
    doc_id: str,
    epub_path: Optional[str],
) -> List[str]:
    """library.process_image_scene_breaks: count repeated images across ALL pages."""
    if not pages:
        return pages
    src_prefix = f"/api/library/image/{doc_id}/"
    symbol_map: Dict[str, str] = {}
    src_counts: Dict[str, int] = {}
    for page_html in pages:
        tree = HTMLParser(page_html)
        for img in tree.css("img, image"):
            if find_parent(img, "s"):
                continue
            src = (img.attributes or {}).get("src", "")
            if src.startswith(src_prefix):
                src_counts[src] = src_counts.get(src, 0) + 1

    for src, count in src_counts.items():
        assigned_id = urllib.parse.unquote(src.replace(src_prefix, ""))
        matched = resolve_image_map_entry(image_map, assigned_id)
        if not matched:
            continue
        img_id, zip_path = matched
        data = _peek_image_bytes(doc_id, img_id, zip_path, epub_path)
        classified = peek_classify_ornament(img_id, data, count)
        if classified["kind"] == "symbols":
            symbol_map[src] = classified["text"]
        elif classified["kind"] == "wrap":
            symbol_map[src] = "@@S_WRAP@@"

    if not symbol_map:
        return pages

    new_pages: List[str] = []
    for page_html in pages:
        if not any(src in page_html for src in symbol_map):
            new_pages.append(page_html)
            continue
        tree = HTMLParser(page_html)
        blocks = nodes_in_document_order(
            tree,
            ("h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "img", "image"),
        )
        for i, tag in enumerate(blocks):
            if tag.tag not in ["img", "image"] or tag.parent is None:
                continue
            src = (tag.attributes or {}).get("src", "")
            if src not in symbol_map:
                continue
            if find_parent(tag, "s") or find_parent(tag, HEADING_TAGS):
                continue
            if not sandwich_allows_scene_break(blocks, i):
                continue
            chars = symbol_map[src]
            top_node = climb_empty_ornament_wrapper(tag)
            if top_node is None or top_node.parent is None:
                continue
            try:
                if chars == "@@S_WRAP@@":
                    _drop_scene_break_target_ids(tag)
                    img_html = tag.html or ""
                    replace_with_html(top_node, f"<s>{img_html}</s>")
                else:
                    replace_with_html(top_node, f"<s>{html.escape(chars)}</s>")
            except Exception:
                pass
        strip_scene_break_inner_wrappers(tree)
        target = tree.body or tree.root
        page_str = (target.html if target else tree.html) or ""
        new_pages.append(re.sub(r">\s*\n+\s*<", "><", page_str))
    return new_pages


def stamp_peek_toc_targets(pages: List[str], toc_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Write target_tts_id onto toc_map for filled pages only (TOC camera)."""
    if not toc_map or not pages:
        return toc_map
    work = [dict(item) for item in toc_map]
    try:
        assign_toc_target_ids(pages, work)
    except Exception as e:
        print(f"[Memories] assign_toc_target_ids failed: {e}")
        return toc_map
    for src, dst in zip(work, toc_map):
        try:
            pidx = int(dst.get("page_index", -1))
        except (TypeError, ValueError):
            continue
        html = pages[pidx] if 0 <= pidx < len(pages) else ""
        if html:
            dst["target_tts_id"] = src.get("target_tts_id")
    return toc_map


def apply_peek_book_passes(
    pages: List[str],
    meta: Dict[str, Any],
    session: Dict[str, Any],
) -> List[str]:
    """Unzip whole-book post-passes: CSS/image scene-break counts, cleanup, lazy/eager."""
    if not pages:
        return pages
    doc_id = str(meta.get("id") or "")
    epub_path = meta.get("source_path") or meta.get("path")
    image_map = meta.get("image_map") or {}
    master_css = session.get("master_css")
    if not master_css:
        master_css = load_master_css(epub_path, meta.get("css") or [])
        session["master_css"] = master_css
    try:
        pages = peek_process_css_scene_breaks(pages, master_css, image_map, doc_id, epub_path)
    except Exception as e:
        print(f"[Memories] CSS scene-break pass failed: {e}")
    try:
        pages = peek_process_image_scene_breaks(pages, image_map, doc_id, epub_path)
    except Exception as e:
        print(f"[Memories] Image scene-break pass failed: {e}")
    try:
        pages = clean_scene_break_contents(pages)
    except Exception as e:
        print(f"[Memories] Scene-break cleanup failed: {e}")
    try:
        pages = apply_image_loading(pages)
    except Exception as e:
        print(f"[Memories] Image loading pass failed: {e}")
    return pages


# ---------------------------------------------------------------------------
# Backward-Compatible Full Content Synthesizer
# ---------------------------------------------------------------------------
def synthesize_full_content(
    doc_id: str,
    meta: Dict[str, Any],
    peeker_caller_fn: Any,
) -> Dict[str, Any]:
    """
    Synthesizes the exact { pages, toc_map, image_map } payload expected by
    the legacy library.js / selectDocument() call completely in RAM.
    Zero files written to disk.
    """
    session = memory_manager.get_session(doc_id) or memory_manager.register_session(doc_id, meta)
    spine = meta.get("spine", [])

    with memory_manager._lock:
        buffered_count = len(session["chapters"])
        status = session.get("status")

    epub_path = meta.get("source_path") or meta.get("path")
    if buffered_count < len(spine) and not source_epub_is_present(epub_path):
        raise FileNotFoundError("Book missing from path")

    # Join the in-flight Tier 2 worker instead of decompressing the book twice.
    if buffered_count < len(spine):
        if status != "buffering":
            memory_manager.start_background_text_buffer(doc_id, meta, peeker_caller_fn)
        memory_manager.wait_for_text_buffer(doc_id, timeout=120.0)
        with memory_manager._lock:
            buffered_count = len(session["chapters"])
            status = session.get("status")

    if status == "missing" or (
        buffered_count < len(spine) and not source_epub_is_present(epub_path)
    ):
        raise FileNotFoundError("Book missing from path")

    if buffered_count < len(spine):
        # Worker timed out or failed: fill remaining chapters on this thread.
        with memory_manager._lock:
            all_raw_htmls = session.get("raw_htmls") or {}
        if len(all_raw_htmls) < len(spine):
            all_raw_htmls = peeker_caller_fn(epub_path, mode="html")
            with memory_manager._lock:
                session["raw_htmls"] = all_raw_htmls
        for idx, href in enumerate(spine):
            with memory_manager._lock:
                if idx in session["chapters"]:
                    continue
            raw_bytes = _raw_for_href(all_raw_htmls, href)
            if raw_bytes:
                cleaned, ch_imgs = memory_manager._run_chapter_pipeline(
                    raw_bytes,
                    href,
                    meta,
                    next_has_header=_spine_next_has_header(spine, idx, all_raw_htmls),
                )
                with memory_manager._lock:
                    session["chapters"][idx] = cleaned

                # Preload initial chapter images into RAM
                if (idx == 0 or idx == meta.get("currentPage", 0)) and ch_imgs and epub_path:
                    for img_id, zip_path in ch_imgs:
                        if not memory_manager.get_cached_image(doc_id, img_id):
                            try:
                                img_bytes = call_peeker_single(epub_path, zip_path)
                                memory_manager.cache_image(doc_id, img_id, img_bytes)
                            except Exception:
                                pass

    with memory_manager._lock:
        compacted = compact_empty_reader_pages(meta, session)
        spine = meta.get("spine", []) or []
        pages = [
            session["chapters"].get(i, "<p>[Missing Chapter]</p>")
            for i in range(len(spine))
        ]

    pages = apply_peek_book_passes(pages, meta, session)
    toc_map = stamp_peek_toc_targets(pages, meta.get("toc_map") or [])
    meta["toc_map"] = toc_map

    with memory_manager._lock:
        for idx, html in enumerate(pages):
            session["chapters"][idx] = html
        session["meta"] = meta
        session["book_passes"] = True
        if len(session["chapters"]) >= len(meta.get("spine") or []):
            session["status"] = "complete"
            event = session.get("filled_event")
            if event is not None:
                event.set()

    try:
        save_book_info(doc_id, meta)
    except Exception as e:
        print(f"[Memories] Failed to persist peek maps for {doc_id}: {e}")

    return {
        "id": doc_id,
        "pages": pages,
        "toc_map": toc_map,
        "footnote_map": meta.get("footnote_map", {}),
        "metadata": meta,
        "image_map": meta.get("image_map", {}),
        "smart_start_page": 0,
        "language": meta.get("language", "en"),
        "bookType": "epub",
        "is_peek": True,
    }
