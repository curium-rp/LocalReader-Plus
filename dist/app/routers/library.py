from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
from collections import defaultdict
import json, re, uuid, posixpath, urllib.parse, shutil, html, zipfile, sys, asyncio, io
from pathlib import Path
from selectolax.parser import HTMLParser, Node
import xml.etree.ElementTree as ET
from ..config import library_file, content_dir, settings_file
from ..models import LibraryItem
from ..utils import (
    safe_save_json,
    language_from_epub_book,
    language_from_html_markup,
    language_from_pages,
    language_from_pdf_doc,
    language_from_text_heuristic,
)

base_dir = Path(__file__).parent.parent
if str(base_dir) not in sys.path:
    sys.path.append(str(base_dir))

try:
    from logic.smart_content_detector import detect_strict_scene_break
    from logic.html_normalizer import (
        generate_toc, pre_parse_clean, normalize_epub_html,
        standardize_footnotes, footnote_number_from_id
    )
except ImportError:
    def footnote_number_from_id(block_id: str) -> str:
        if not block_id:
            return ""
        match = re.search(r"(\d+)(?:sym|anc)?$", block_id or "", flags=re.I)
        return match.group(1) if match else ""

# Yield direct children of a node (text nodes optional)
def iter_children(node: Optional[Node], include_text: bool = False):
    """Yield immediate children; Node.iter() would duplicate descendants."""
    if node is None:
        return
    child = node.child
    while child:
        next_child = child.next
        if include_text or child.tag != "-text":
            yield child
        child = next_child

# Collect all matching tags in document order
def nodes_in_document_order(tree, tags) -> List[Node]:
    """Match BeautifulSoup.find_all(tag_list) ordering with Selectolax."""
    wanted = set(tags)
    root = (tree.body or tree.root) if isinstance(tree, HTMLParser) else tree
    if root is None:
        return []
    ordered = []

    def visit(parent: Node) -> None:
        child = parent.child
        while child is not None:
            next_child = child.next
            if child.tag in wanted:
                ordered.append(child)
            visit(child)
            child = next_child

    visit(root)
    return ordered

# Return serialized inner HTML string of a node
def get_inner_html(node: Optional[Node]) -> str:
    if not node:
        return ""
    return "".join(
        child.html for child in iter_children(node, include_text=True)
        if child.html is not None
    )

# Replace a node with parsed markup, including multiple siblings
def replace_with_html(node: Optional[Node], markup: str) -> None:
    """Replace a node with parsed markup, including multiple siblings."""
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

# Unwrap a node lifting children into parent (safe guard)
def safe_unwrap(node: Optional[Node]) -> None:
    if not node or node.parent is None or node.tag in ("body", "html", "head"):
        return
    node.unwrap()

# Serialize attribute dict to an HTML attribute string
def _attrs_to_str(attrs) -> str:
    if not attrs:
        return ""
    return "".join(
        f' {key}="{html.escape(str(value), quote=True)}"'
        for key, value in attrs.items() if value is not None
    )

# Unwrap a node inserting raw text markers before/after it
def safe_unwrap_and_mark(
    node: Optional[Node], prefix: str, suffix: str
) -> None:
    if not node or node.parent is None or node.tag in ("body", "html", "head", None):
        return
    if prefix:
        node.insert_before(prefix)
    if suffix:
        node.insert_after(suffix)
    node.unwrap()

# Walk ancestors to find first matching tag
def find_parent(node: Optional[Node], tags) -> Optional[Node]:
    wanted = {tags} if isinstance(tags, str) else set(tags)
    parent = node.parent if node is not None else None
    while parent is not None:
        if parent.tag in wanted:
            return parent
        parent = parent.parent
    return None

# Insert text before the first child of a node
def prepend_text(node: Node, text: str) -> None:
    if node.child is not None:
        node.child.insert_before(text)
    else:
        node.insert_child(text)

# Insert text after the last child of a node
def append_text(node: Node, text: str) -> None:
    if node.last_child is not None:
        node.last_child.insert_after(text)
    else:
        node.insert_child(text)


_RUBY_REL = re.compile(r"position\s*:\s*relative", re.I)
_RUBY_ABS = re.compile(r"position\s*:\s*absolute", re.I)
_RUBY_NOWRAP = re.compile(r"white-space\s*:\s*nowrap", re.I)
_RUBY_NEG_TOP = re.compile(r"top\s*:\s*-\s*[\d.]+", re.I)
_RUBY_MARKER_RE = re.compile(
    r"@@RUBY_S@@(.*?)@@RT_S@@(.*?)@@RT_E@@@@RUBY_E@@",
    re.DOTALL,
)


def _node_style(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return (node.attributes or {}).get("style") or ""


def _collect_text_excluding(node: Optional[Node], skip_tags) -> str:
    if node is None:
        return ""
    skip = set(skip_tags)
    parts = []

    def walk(parent: Node) -> None:
        child = parent.child
        while child is not None:
            nxt = child.next
            tag = child.tag
            if tag == "-text":
                parts.append(child.text(strip=False) or "")
            elif tag not in skip:
                walk(child)
            child = nxt

    walk(node)
    return "".join(parts)


def _sanitize_ruby_text(text: str) -> str:
    return (text or "").replace("@@", "").strip()


def _ruby_marker(base: str, annotation: str) -> str:
    return (
        f"@@RUBY_S@@{_sanitize_ruby_text(base)}"
        f"@@RT_S@@{_sanitize_ruby_text(annotation)}@@RT_E@@@@RUBY_E@@"
    )


def _replace_node_with_text(node: Optional[Node], text: str) -> None:
    if not node or node.parent is None:
        return
    node.insert_before(text)
    node.decompose()


def _protect_native_ruby(tree: HTMLParser) -> None:
    for ruby in list(tree.css("ruby")):
        if ruby.parent is None:
            continue
        annotation = "".join(
            (rt.text(separator="", strip=True) or "").strip()
            for rt in ruby.css("rt")
        )
        base = _collect_text_excluding(ruby, ("rt", "rp")).strip()
        if not base or not annotation:
            continue
        _replace_node_with_text(ruby, _ruby_marker(base, annotation))


def _protect_fake_ruby(tree: HTMLParser) -> None:
    for span in list(tree.css("span")):
        if span.parent is None:
            continue
        style = _node_style(span)
        if not (_RUBY_REL.search(style) and _RUBY_NOWRAP.search(style)):
            continue
        annotation = None
        bases = []
        for child in iter_children(span, include_text=False):
            child_style = _node_style(child)
            if _RUBY_ABS.search(child_style) and _RUBY_NEG_TOP.search(child_style):
                annotation = child
            else:
                bases.append(child)
        if annotation is None or not bases:
            continue
        annotation_text = (annotation.text(separator="", strip=True) or "").strip()
        base_text = "".join(
            (child.text(separator="", strip=True) or "") for child in bases
        ).strip()
        if not annotation_text or not base_text:
            continue
        _replace_node_with_text(span, _ruby_marker(base_text, annotation_text))



ITEM_UNKNOWN = 0
ITEM_IMAGE = 1
ITEM_STYLE = 2
ITEM_DOCUMENT = 9


# TOC entry model holding title and href
class TocItem:
    def __init__(self, title: str, href: str):
        self.title = title
        self.href = href


# EPUB item holding manifest metadata and file content
class NativeEpubItem:
    def __init__(self, item_id: str, href: str, full_path: str, media_type: str, content: bytes):
        self.id = item_id
        self.href = href
        self.full_path = full_path
        self.file_name = full_path
        self.media_type = media_type
        self._content = content

    def get_name(self) -> str:
        return self.full_path

    def get_content(self) -> bytes:
        return self._content

    def get_type(self) -> int:
        media_type = (self.media_type or "").lower()
        name = (self.full_path or self.href or "").lower()
        # Images first: image/svg+xml contains "xml" and must not become ITEM_DOCUMENT.
        if media_type.startswith("image/") or name.endswith((
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg",
        )):
            return ITEM_IMAGE
        if "css" in media_type or name.endswith(".css"):
            return ITEM_STYLE
        if (
            "xhtml" in media_type
            or media_type in ("text/html", "application/html")
            or name.endswith((".xhtml", ".html", ".htm"))
        ):
            return ITEM_DOCUMENT
        if "html" in media_type:
            return ITEM_DOCUMENT
        return ITEM_UNKNOWN


# Open EPUB zip and parse manifest/spine/metadata/TOC
class NativeEpub:
    def __init__(self, file_source):
        self._file_source = file_source
        self.archive = zipfile.ZipFile(file_source, "r")
        self._namelist = self.archive.namelist()
        self._name_lookup = {name.lower(): name for name in self._namelist}
        self._basename_lookup = {posixpath.basename(name).lower(): name for name in self._namelist}

        self.items_list: List[NativeEpubItem] = []
        self.items_by_id: Dict[str, NativeEpubItem] = {}
        self.items_by_path: Dict[str, NativeEpubItem] = {}
        self.items_by_href: Dict[str, NativeEpubItem] = {}

        self.spine: List[tuple] = []
        self.toc: list = []
        self.language: str = ""
        self.title: str = ""
        self.opf_dir: str = ""

        try:
            self._load()
        except Exception:
            self.close()
            raise

    # Read raw bytes from zip by exact or case-folded name
    def _read_raw(self, path: str) -> Optional[bytes]:
        clean = posixpath.normpath(path).lstrip("/")
        real_name = self._name_lookup.get(clean.lower())
        if not real_name:
            real_name = self._basename_lookup.get(posixpath.basename(clean).lower())
        if real_name:
            try:
                return self.archive.read(real_name)
            except Exception:
                return None
        return None

    # Parse OPF manifest/spine/metadata into item list
    def _load(self):
        container_bytes = self._read_raw("META-INF/container.xml")
        if not container_bytes:
            raise ValueError("Invalid EPUB: missing META-INF/container.xml")

        container_xml = ET.fromstring(container_bytes)
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None or "full-path" not in rootfile.attrib:
            raise ValueError("Invalid EPUB: missing rootfile specification")

        opf_path = posixpath.normpath(urllib.parse.unquote(rootfile.attrib["full-path"])).lstrip("/")
        self.opf_dir = posixpath.dirname(opf_path)

        opf_bytes = self._read_raw(opf_path)
        if not opf_bytes:
            raise ValueError(f"Invalid EPUB: package file not found at {opf_path}")

        opf_xml = ET.fromstring(opf_bytes)

        lang_node = opf_xml.find(".//{*}metadata/{*}language")
        if lang_node is not None and lang_node.text:
            self.language = lang_node.text.strip()

        title_node = opf_xml.find(".//{*}metadata/{*}title")
        if title_node is not None and title_node.text:
            self.title = title_node.text.strip()

        ncx_path = ""
        nav_path = ""

        for item in opf_xml.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id", "")
            raw_href = item.attrib.get("href", "")
            href = urllib.parse.unquote(raw_href.split("#")[0].split("?")[0])
            media_type = item.attrib.get("media-type", "")
            properties = item.attrib.get("properties", "")

            full_path = posixpath.normpath(posixpath.join(self.opf_dir, href)).lstrip("/")
            content = self._read_raw(full_path) or b""

            epub_item = NativeEpubItem(
                item_id=item_id,
                href=href,
                full_path=full_path,
                media_type=media_type,
                content=content,
            )

            self.items_list.append(epub_item)
            if item_id:
                self.items_by_id[item_id] = epub_item
            self.items_by_path[full_path.lower()] = epub_item
            self.items_by_href[href.lower()] = epub_item

            if media_type == "application/x-dtbncx+xml" or href.lower().endswith(".ncx"):
                ncx_path = full_path
            if "nav" in properties.split() or href.lower().endswith("nav.xhtml"):
                nav_path = full_path

        for itemref in opf_xml.findall(".//{*}spine/{*}itemref"):
            idref = itemref.attrib.get("idref")
            if idref:
                self.spine.append((idref, itemref.attrib.get("linear", "yes")))

        if ncx_path:
            ncx_bytes = self._read_raw(ncx_path)
            if ncx_bytes:
                try:
                    self.toc = self._parse_ncx(ncx_bytes, posixpath.dirname(ncx_path))
                except Exception:
                    self.toc = []

        if not self.toc and nav_path:
            nav_bytes = self._read_raw(nav_path)
            if nav_bytes:
                try:
                    self.toc = self._parse_nav_doc(nav_bytes, posixpath.dirname(nav_path))
                except Exception:
                    self.toc = []

    # Parse NCX toc.ncx into TocItem list
    def _parse_ncx(self, ncx_bytes: bytes, ncx_dir: str) -> list:
        root = ET.fromstring(ncx_bytes)

        def parse_navpoint(node):
            label = node.find("{*}navLabel/{*}text")
            title = label.text.strip() if (label is not None and label.text) else ""
            content_node = node.find("{*}content")
            src = content_node.attrib.get("src", "") if content_node is not None else ""
            full_href = posixpath.normpath(posixpath.join(ncx_dir, src)).lstrip("/") if src else ""

            children = [parse_navpoint(child) for child in node.findall("{*}navPoint")]
            toc_item = TocItem(title=title, href=full_href)
            if children:
                return (toc_item, children)
            return toc_item

        navmap = root.find(".//{*}navMap")
        if navmap is None:
            return []
        return [parse_navpoint(node) for node in navmap.findall("{*}navPoint")]

    # Parse EPUB3 nav document into TocItem list
    def _parse_nav_doc(self, nav_bytes: bytes, nav_dir: str) -> list:
        tree = HTMLParser(nav_bytes.decode("utf-8", errors="ignore"))
        nav = None
        for candidate in tree.css("nav"):
            attrs = candidate.attributes or {}
            epub_type = " ".join(
                str(value)
                for key, value in attrs.items()
                if value and (key.lower() in ("epub:type", "type") or key.lower().endswith(":type"))
            ).lower()
            nav_id = (attrs.get("id") or "").lower()
            nav_class = (attrs.get("class") or "").lower()
            if "toc" in epub_type.split() or nav_id == "toc" or "toc" in nav_class.split():
                nav = candidate
                break
        if not nav:
            nav = tree.css_first("nav")
        if not nav:
            return []
        ol = nav.css_first("ol")
        if not ol:
            return []

        def parse_ol(ol_node):
            result = []
            for li in iter_children(ol_node):
                if li.tag != "li":
                    continue
                anchor = li.css_first("a")
                if not anchor:
                    continue
                title = anchor.text(strip=True)
                raw_href = (anchor.attributes or {}).get("href", "")
                full_href = posixpath.normpath(posixpath.join(nav_dir, raw_href)).lstrip("/") if raw_href else ""
                item = TocItem(title=title, href=full_href)
                sub_ol = next((child for child in iter_children(li) if child.tag == "ol"), None)
                if sub_ol:
                    result.append((item, parse_ol(sub_ol)))
                else:
                    result.append(item)
            return result

        return parse_ol(ol)

    # Yield all manifest items
    def get_items(self) -> List[NativeEpubItem]:
        return self.items_list

    # Find item by manifest ID
    def get_item_with_id(self, item_id: str) -> Optional[NativeEpubItem]:
        return self.items_by_id.get(item_id)

    # Find item by normalized href
    def get_item_with_href(self, href: str) -> Optional[NativeEpubItem]:
        clean = posixpath.normpath(href).lstrip("/").lower()
        if clean in self.items_by_path:
            return self.items_by_path[clean]
        if clean in self.items_by_href:
            return self.items_by_href[clean]
        target_base = posixpath.basename(clean)
        for item in self.items_list:
            if (
                posixpath.basename(item.full_path).lower() == target_base
                or posixpath.basename(item.href).lower() == target_base
            ):
                return item
        return None

    # Yield items matching a media type constant
    def get_items_of_type(self, item_type: int) -> List[NativeEpubItem]:
        return [item for item in self.items_list if item.get_type() == item_type]

    # Read a metadata field (e.g. language, title) from OPF
    def get_metadata(self, namespace: str, name: str) -> list:
        if namespace == "DC" and name == "language":
            return [(self.language, {})] if self.language else []
        if namespace == "DC" and name == "title":
            return [(self.title, {})] if self.title else []
        return []

    # Try to read a zip file not listed in manifest (rescue path)
    def read_unmanifested_file(self, filename: str) -> Optional[bytes]:
        return self._read_raw(filename)

    # Close the underlying zip file handle
    def close(self):
        try:
            self.archive.close()
        except Exception:
            pass

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

# Locate JSON metadata file for a given document ID
def get_doc_json_path(doc_id: str) -> Path:
    p = content_dir / doc_id / f"{doc_id}.json"
    if p.exists(): return p
    p = content_dir / f"{doc_id}.json"
    if p.exists(): return p
    raise HTTPException(404, "Document not found")

# Inject protection before burn
def force_formatting_markers(tree: HTMLParser, current_href: str = "") -> None:
    """
    Force every formatting tag and style into indestructible markers.
    Runs on the live tree as early as possible.
    """
    def make_det_id(target_file, target_id):
        if not target_file: target_file = current_href
        elif target_file != current_href and current_href:
            base_dir = posixpath.dirname(current_href)
            target_file = posixpath.normpath(posixpath.join(base_dir, target_file))

        safe_file = re.sub(r'[^a-zA-Z0-9]', '_', target_file)
        safe_id = re.sub(r'[^a-zA-Z0-9]', '_', target_id)
        return f"R_{safe_file}_{safe_id}"

    # 🌟 EXTERNAL LINK SHIELD
    for anchor in list(tree.css("a")):
        if anchor.parent is None:
            continue
        href = (anchor.attributes or {}).get("href", "").strip()
        if href.startswith(("http://", "https://", "mailto:")):
            safe_href = urllib.parse.quote(href, safe="")
            prepend_text(anchor, f"@@EXT_A|{safe_href}@@")
            append_text(anchor, "@@EXT_A_OFF@@")
            safe_unwrap(anchor)

    # 🌟 FOOTNOTE EXTRACTION SHIELD
    # Accepts epub:type from standardize_footnotes and DPUB ARIA roles as fallback.
    for anchor in list(tree.css("a")):
        if anchor.parent is None:
            continue
        attrs = anchor.attributes or {}
        epub_type = (attrs.get("epub:type") or "").lower()
        role = (attrs.get("role") or "").lower()
        if epub_type != "noteref" and role != "doc-noteref":
            continue
        href = attrs.get("href", "")
        if "#" not in href:
            continue

        parts = href.split("#")
        if len(parts) > 1:
            det_id = make_det_id(parts[0], parts[1])
            sup_tag = find_parent(anchor, "sup") or anchor.css_first("sup")
            tag_type = "SUP" if sup_tag else "A"

            prepend_text(anchor, f"@@F_ON@@ @@F_S|{det_id}|{tag_type}@@ ")
            append_text(anchor, f" @@F_OFF_{tag_type}@@")

            if sup_tag:
                safe_unwrap(sup_tag)
            safe_unwrap(anchor)

    for block in list(tree.css("*")):
        if block.parent is None:
            continue
        attrs = block.attributes or {}
        epub_type = (attrs.get("epub:type") or "").lower()
        role = (attrs.get("role") or "").lower()
        if epub_type != "footnote" and role not in ("doc-footnote", "doc-endnote"):
            continue
        block_id = attrs.get("id", "")
        if "@@F_ON@@" in block.text():
            continue

        backlink = next(
            (
                anchor for anchor in block.css("a")
                if (anchor.attributes or {}).get("epub:type") == "backlink"
                or (anchor.attributes or {}).get("role") == "doc-backlink"
            ),
            None,
        )
        if not block_id and backlink:
            backlink_attrs = backlink.attributes or {}
            block_id = backlink_attrs.get("id") or backlink_attrs.get("name") or ""
        if not block_id:
            continue

        det_id = make_det_id(current_href, block_id)
        note_num = footnote_number_from_id(block_id)
        backlink_text = backlink.text(strip=True) if backlink else ""
        remainder = (block.text(strip=True) or "")
        if backlink_text and remainder.startswith(backlink_text):
            remainder = remainder[len(backlink_text):].lstrip(" .:]-")
        needs_label = bool(
            note_num
            and not re.search(r"\d+", backlink_text)
            and not re.match(rf"^[\[\(]?{re.escape(note_num)}\b", remainder)
        )
        if backlink:
            prepend_text(backlink, f"@@F_ON@@ @@F_E|{det_id}@@ ")
            append_text(backlink, " @@F_OFF_A@@")
            if needs_label:
                backlink.insert_after(f"[{note_num}] ")
            safe_unwrap(backlink)
        else:
            inner_nodes = nodes_in_document_order(block, ("p", "span", "div"))
            inner = inner_nodes[0] if inner_nodes else block
            label = f"[{note_num}] " if needs_label else ""
            prepend_text(inner, f"@@F_ON@@ @@F_E|{det_id}@@ @@F_OFF_A@@ {label}")

    # 🌟 RUBY SHIELD (native <ruby> and CSS furigana hacks)
    _protect_native_ruby(tree)
    _protect_fake_ruby(tree)

    # 🌟 FORMATTING SHIELD (RESTORED)
    bold_regex = re.compile(r'\b(bold|bld|strong|calibre_bold|fw-bold|font-bold|b-text)\b', re.IGNORECASE)
    ital_regex = re.compile(r'\b(italic|it|em|emphasis|oblique|calibre_italic|fs-italic|i-text)\b', re.IGNORECASE)
    und_regex = re.compile(r'\b(underline|u-text|calibre_under)\b', re.IGNORECASE)
    del_regex = re.compile(r'\b(strike|strikethrough|line-through|del)\b', re.IGNORECASE)

    for tag in list(tree.css("span, font, p, div, a")):
        if tag.parent is None:
            continue
        attrs = tag.attributes or {}
        style = (attrs.get("style") or "").lower()
        class_str = (attrs.get("class") or "").lower()

        is_bold = 'bold' in style or '600' in style or '700' in style or '800' in style or '900' in style or 'bolder' in style or bold_regex.search(class_str)
        is_ital = 'italic' in style or 'oblique' in style or ital_regex.search(class_str)
        is_und = 'underline' in style or und_regex.search(class_str)
        is_del = 'line-through' in style or del_regex.search(class_str)

        if is_bold or is_ital or is_und or is_del:
            if is_bold:
                prepend_text(tag, "@@B_ON@@")
                append_text(tag, "@@B_OFF@@")
            if is_ital:
                prepend_text(tag, "@@I_ON@@")
                append_text(tag, "@@I_OFF@@")
            if is_und:
                prepend_text(tag, "@@U_ON@@")
                append_text(tag, "@@U_OFF@@")
            if is_del:
                prepend_text(tag, "@@D_ON@@")
                append_text(tag, "@@D_OFF@@")

            if "style" in (tag.attributes or {}):
                del tag.attrs["style"]
            if "class" in (tag.attributes or {}):
                del tag.attrs["class"]

            if tag.tag in ("span", "font"):
                safe_unwrap(tag)

    mapping = [
        (("b", "strong"), "@@B_ON@@", "@@B_OFF@@"),
        (("i", "em", "cite", "dfn"), "@@I_ON@@", "@@I_OFF@@"),
        (("u", "ins"), "@@U_ON@@", "@@U_OFF@@"),
        (("del", "s", "strike"), "@@D_ON@@", "@@D_OFF@@"),
    ]

    for tags, on, off in mapping:
        for tag in list(tree.css(", ".join(tags))):
            safe_unwrap_and_mark(tag, on, off)

    for line_break in list(tree.css("br")):
        line_break.insert_before("@@BR@@")
        line_break.decompose()


# Restore wrap: convert @@ protection markers back into real HTML tags
def restore_inline_markers(markup: str) -> str:
    restored = (
        markup
        .replace("@@B_ON@@", "<b>").replace("@@B_OFF@@", "</b>")
        .replace("@@I_ON@@", "<i>").replace("@@I_OFF@@", "</i>")
        .replace("@@U_ON@@", "<u>").replace("@@U_OFF@@", "</u>")
        .replace("@@D_ON@@", "<del>").replace("@@D_OFF@@", "</del>")
        .replace("@@F_ON@@ ", "").replace("@@F_ON@@", "")
        .replace(" @@F_OFF_A@@", "</a>").replace("@@F_OFF_A@@", "</a>")
        .replace(" @@F_OFF_SUP@@", "</sup></a>")
        .replace("@@F_OFF_SUP@@", "</sup></a>")
        .replace(" @@BR@@ ", "<br>").replace("@@BR@@", "<br>")
    )
    restored = re.sub(
        r"@@F_S\|([^@|]*)\|SUP@@\s*",
        r'<a epub:type="noteref" href="#\1"><sup>',
        restored,
    )
    restored = re.sub(
        r"@@F_S\|([^@|]*)\|A@@\s*",
        r'<a epub:type="noteref" href="#\1">',
        restored,
    )
    restored = re.sub(
        r"@@F_E\|([^@|]*)@@\s*",
        r'<a epub:type="footnote" id="\1">',
        restored,
    )
    restored = _RUBY_MARKER_RE.sub(
        lambda match: (
            f"<ruby>{html.escape(html.unescape(match.group(1)), quote=False)}"
            f"<rp>(</rp><rt>{html.escape(html.unescape(match.group(2)), quote=False)}</rt>"
            f"<rp>)</rp></ruby>"
        ),
        restored,
    )
    # 🌟 RESTORE EXTERNAL LINKS
    def _restore_ext_link(match):
        raw_url = urllib.parse.unquote(match.group(1))
        inner_text = match.group(2)
        safe_url = html.escape(raw_url, quote=True)
        return f'<a href="{safe_url}" class="external-link" target="_blank" rel="noopener noreferrer">{inner_text}</a>'

    return re.sub(
        r"@@EXT_A\|([^@|]+)@@(.*?)@@EXT_A_OFF@@",
        _restore_ext_link,
        restored,
        flags=re.DOTALL,
    )


# Inject s_ IDs onto all leaf text/heading/media blocks
def assign_epub_block_ids(tree: HTMLParser) -> int:
    """Assign one TTS block ID per source block without sentence splitting."""
    global_idx = 0
    heading_tags = ("h1", "h2", "h3", "h4", "h5", "h6")
    block_tags = (
        "p", "div", *heading_tags, "li", "blockquote", "figure",
        "aside", "article", "section", "main", "pre", "address",
        "dd", "dt", "figcaption", "summary",
    )
    nested_tags = (
        "p", "div", "ul", "ol", "table", "blockquote", "figure",
        "aside", "article", "section", "main", "pre", "address",
        "dd", "dt", "figcaption", "summary", *heading_tags,
    )

    for block in nodes_in_document_order(tree, block_tags):
        if block.parent is None or nodes_in_document_order(block, nested_tags):
            continue

        media_nodes = nodes_in_document_order(
            block, ("img", "s", "picture", "svg", "figure")
        )
        text = block.text(separator=" ", strip=True)
        if media_nodes and not text:
            attrs = dict(block.attributes or {})
            original_id = attrs.get("id")
            for anchor in nodes_in_document_order(block, ("a",)):
                if not original_id:
                    original_id = (anchor.attributes or {}).get("id")
                safe_unwrap(anchor)
            for attribute in ("class", "style", "lang", "dir"):
                attrs.pop(attribute, None)
            attrs["id"] = f"s_{global_idx}"
            if original_id:
                attrs["data-orig-id"] = original_id
            replace_with_html(
                block,
                f"<{block.tag}{_attrs_to_str(attrs)}>"
                f"{get_inner_html(block)}</{block.tag}>",
            )
            global_idx += 1
            continue
        if not text:
            continue

        attrs = dict(block.attributes or {})
        original_id = attrs.pop("id", None)
        for attribute in ("class", "style", "lang", "dir"):
            attrs.pop(attribute, None)

        reader_attrs = {"id": f"s_{global_idx}"}
        if original_id:
            reader_attrs["data-orig-id"] = original_id
        inner_html = get_inner_html(block)

        if block.tag in heading_tags:
            attrs.update(reader_attrs)
            replacement = (
                f"<{block.tag}{_attrs_to_str(attrs)}>"
                f"{inner_html}</{block.tag}>"
            )
        elif block.tag == "p":
            attrs.update(reader_attrs)
            replacement = f"<p{_attrs_to_str(attrs)}>{inner_html}</p>"
        elif block.tag in ("li", "blockquote", "figure"):
            replacement = (
                f"<{block.tag}{_attrs_to_str(attrs)}>"
                f"<p{_attrs_to_str(reader_attrs)}>{inner_html}</p>"
                f"</{block.tag}>"
            )
        else:
            attrs.update(reader_attrs)
            replacement = f"<p{_attrs_to_str(attrs)}>{inner_html}</p>"

        replace_with_html(block, replacement)
        global_idx += 1

    return global_idx


# Strip punctuation from TOC title for fuzzy matching
def _normalize_toc_title(text: str) -> str:
    return re.sub(r"[^\w]", "", (text or "").lower())


# Compare cleaned TOC title against heading text
def _toc_titles_match(clean_title: str, heading_text: str) -> bool:
    """Match TOC titles to headings without empty/prefix false positives."""
    if not clean_title or not heading_text:
        return False
    if clean_title == heading_text or clean_title in heading_text:
        return True
    min_len = max(8, len(clean_title) // 2)
    return heading_text in clean_title and len(heading_text) >= min_len


# Read the s_ TTS id from a node, None if absent
def _node_tts_id(node: Optional[Node]) -> Optional[str]:
    if node is None:
        return None
    node_id = (node.attributes or {}).get("id") or ""
    return node_id if node_id.startswith("s_") else None


# Derive normalized key string from heading for dedup matching
def _heading_text_key(heading: Node) -> str:
    return _normalize_toc_title(heading.text(strip=True))


# Find first s_ id under node not already claimed by TOC
def _first_unclaimed_tts_id(node, claimed_ids: set, tags: Optional[tuple] = None) -> Optional[str]:
    root = (node.body or node.root) if isinstance(node, HTMLParser) else node
    if root is None:
        return None
    if tags:
        for child in nodes_in_document_order(root, tags):
            child_id = _node_tts_id(child)
            if child_id and child_id not in claimed_ids:
                return child_id
        return None

    ordered: List[Node] = []

    def visit(parent: Node) -> None:
        child = parent.child
        while child is not None:
            next_child = child.next
            if child.tag != "-text":
                ordered.append(child)
            visit(child)
            child = next_child

    visit(root)
    for child in ordered:
        child_id = _node_tts_id(child)
        if child_id and child_id not in claimed_ids:
            return child_id
    return None


# Locate element by anchor fragment in page tree
def _anchor_element(page_tree: HTMLParser, anchor: str) -> Optional[Node]:
    clean_anchor = (anchor or "").split("#")[-1]
    if not clean_anchor:
        return None
    safe = clean_anchor.replace("\\", "\\\\").replace('"', '\\"')
    return page_tree.css_first(
        f'[data-orig-id="{safe}"], [id="{safe}"]'
    )


# Resolve anchor/title to the correct TTS sentence id for TOC linking
def _anchor_tts_id(
    page_tree: HTMLParser,
    toc_item: Dict[str, Any],
    claimed_ids: set,
    clean_title: str,
    heading_tags: tuple,
) -> Optional[str]:
    """Resolve a fragment only when it agrees with the TOC title."""
    el = _anchor_element(page_tree, toc_item.get("anchor_id") or "")
    if el is None:
        return None

    heading_id = _node_tts_id(el)
    if heading_id and heading_id not in claimed_ids:
        if el.tag in heading_tags:
            heading_text = _heading_text_key(el)
            if heading_text and clean_title and not _toc_titles_match(clean_title, heading_text):
                return None
        return heading_id

    for heading in nodes_in_document_order(el, heading_tags):
        heading_id = _node_tts_id(heading)
        if not heading_id or heading_id in claimed_ids:
            continue
        heading_text = _heading_text_key(heading)
        if clean_title and heading_text and _toc_titles_match(clean_title, heading_text):
            return heading_id
    return None


# Walk all pages linking each TOC entry to its s_ target sentence id
def assign_toc_target_ids(pages: List[str], toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map each TOC entry to a TTS block ID on its own page.

    EPUB block IDs reset per HTML file, so claimed IDs are tracked per
    page_index rather than globally. Title matches beat raw anchors so a
    leftover fragment on the next heading cannot steal this entry.
    """
    claimed_by_page: Dict[int, set] = {}
    heading_tags = ("h1", "h2", "h3", "h4", "h5", "h6")
    pending: List[tuple] = []

    for toc_item in toc:
        p_idx = toc_item.get("page_index", 0)
        if p_idx < 0 or p_idx >= len(pages):
            toc_item["target_tts_id"] = None
            continue

        claimed_ids = claimed_by_page.setdefault(p_idx, set())
        page_tree = HTMLParser(pages[p_idx])
        clean_title = _normalize_toc_title(toc_item.get("title") or "")
        target_tts_id = None

        if clean_title:
            for heading in nodes_in_document_order(page_tree, heading_tags):
                heading_id = _node_tts_id(heading)
                if not heading_id or heading_id in claimed_ids:
                    continue
                if _toc_titles_match(clean_title, _heading_text_key(heading)):
                    target_tts_id = heading_id
                    break

        if not target_tts_id:
            target_tts_id = _anchor_tts_id(
                page_tree, toc_item, claimed_ids, clean_title, heading_tags
            )

        if target_tts_id:
            claimed_ids.add(target_tts_id)
            toc_item["target_tts_id"] = target_tts_id
        else:
            toc_item["target_tts_id"] = None
            pending.append((toc_item, p_idx))

    for toc_item, p_idx in pending:
        claimed_ids = claimed_by_page.setdefault(p_idx, set())
        page_tree = HTMLParser(pages[p_idx])
        target_tts_id = _first_unclaimed_tts_id(page_tree, claimed_ids, heading_tags)
        if not target_tts_id:
            target_tts_id = _first_unclaimed_tts_id(page_tree, claimed_ids)
        if target_tts_id:
            claimed_ids.add(target_tts_id)
        toc_item["target_tts_id"] = target_tts_id

    return toc


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


# Read image width and height from file header
def get_image_size(filepath: Path):
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
            if data.startswith(b'\x89PNG\r\n\x1a\n'):
                import struct
                w, h = struct.unpack('>LL', data[16:24])
                return w, h
            elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
                import struct
                w, h = struct.unpack('<HH', data[6:10])
                return w, h
            elif data.startswith(b'\xff\xd8'):
                i = 2
                while i < len(data):
                    while i < len(data) and data[i] == 0xFF: i += 1
                    if i >= len(data): break
                    marker = data[i]
                    i += 1
                    if 0xC0 <= marker <= 0xC3:
                        h = (data[i+3] << 8) + data[i+4]
                        w = (data[i+5] << 8) + data[i+6]
                        return w, h
                    else:
                        length = (data[i] << 8) + data[i+1]
                        i += length
    except Exception:
        pass
    return 0, 0


HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
ORNAMENT_KEYWORDS = (
    "circle", "box", "square", "star", "break", "line", "ornament",
    "orn", "sep", "div", "divider", "fleuron", "diamond", "decoration",
)
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]+)['"]?\s*\)""", re.I)
CSS_ORNAMENT_KEYWORDS = ("background", "url(", "content:", "image", "list-style")


# Classify an image as a scene-break ornament, wrap, or narrative
def classify_ornament_image(filename, img_path, count, wrap_unidentified=None):
    """Classify a lone ornament image as unicode symbols, wrap-in-s, or skip."""
    clues = re.split(r"[^a-zA-Z0-9]", str(filename).lower())
    is_symbolic = False
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
    w, h = get_image_size(img_path)
    symbol_count = 1

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

    should_wrap = (count > 5) if wrap_unidentified is None else wrap_unidentified
    if w and h and w <= 150 and h <= 150 and should_wrap:
        return {"kind": "wrap"}
    return {"kind": "skip"}


# True if node is or is a direct child of a heading tag
def block_is_or_in_heading(node) -> bool:
    if node is None:
        return False
    if node.tag in HEADING_TAGS:
        return True
    return find_parent(node, HEADING_TAGS) is not None


# Walk blocks list left/right from start to find the first with text
def nearest_text_block(blocks, start, step):
    idx = start
    while 0 <= idx < len(blocks):
        if blocks[idx].text(strip=True):
            return blocks[idx]
        idx += step
    return None


# True if surrounding blocks are text, allowing a scene break here
def sandwich_allows_scene_break(blocks, index) -> bool:
    """True only when the node sits between content and is not next to a heading."""
    prev_node = nearest_text_block(blocks, index - 1, -1)
    next_node = nearest_text_block(blocks, index + 1, 1)
    if not prev_node or not next_node:
        return False
    if block_is_or_in_heading(prev_node) or block_is_or_in_heading(next_node):
        return False
    return True


# Extract first url() value from a block's data-orig-class CSS
def first_css_url(block: str) -> str:
    match = CSS_URL_RE.search(block or "")
    if not match:
        return ""
    url = match.group(1).strip()
    if url.lower().startswith(("data:", "http:", "https:", "file:")):
        return ""
    return url.split("#")[0].split("?")[0]


# Save image bytes to disk and register filename in image_map
def register_extracted_image(image_content, actual_item_name, book_dir, image_map):
    """Save EPUB image bytes into book_dir and return the mapped filename."""
    if not image_content or not book_dir:
        return None
    clean_name = str(actual_item_name or "ornament").split("?")[0].split("#")[0]
    base_name = posixpath.splitext(clean_name)[0]
    extension = posixpath.splitext(clean_name)[1].lower()
    safe_base = re.sub(
        r'[\\/*?:"<>|]',
        "",
        base_name.replace("/", "_").replace("\\", "_"),
    )
    if len(safe_base) > 50:
        safe_base = safe_base[:40] + "_" + uuid.uuid4().hex[:6]
    elif not safe_base:
        safe_base = f"img_{uuid.uuid4().hex[:8]}"
    if extension not in [".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"]:
        extension = ".jpg"
    safe_filename = f"{safe_base}{extension}"

    if image_map is not None and safe_filename in image_map:
        return safe_filename

    image_path = Path(book_dir) / safe_filename
    if not image_path.exists():
        try:
            image_path.write_bytes(image_content)
        except Exception as error:
            print(f"[Warning] Failed to save CSS ornament {safe_filename}: {error}")
            return None
    if image_map is not None:
        image_map[safe_filename] = safe_filename
    return safe_filename


# Resolve a CSS background url() to book image bytes
def resolve_css_image_url(url_path, book, book_dir, image_map):
    """Find a CSS background image already on disk, or extract it from the EPUB."""
    basename = posixpath.basename(urllib.parse.unquote(url_path or "")).lower()
    if not basename:
        return None

    if image_map:
        for key, value in image_map.items():
            for candidate in (key, value):
                if posixpath.basename(str(candidate)).lower() == basename:
                    return value or key

    if book_dir:
        book_path = Path(book_dir)
        if book_path.exists():
            for existing in book_path.iterdir():
                if existing.is_file() and existing.name.lower() == basename:
                    if image_map is not None:
                        image_map.setdefault(existing.name, existing.name)
                    return existing.name

    image_item = None
    if book is not None:
        try:
            for candidate in book.get_items():
                if posixpath.basename(candidate.get_name()).lower() == basename:
                    image_item = candidate
                    break
        except Exception:
            image_item = None

    if not image_item:
        return None
    try:
        content = image_item.get_content()
        name = image_item.get_name()
    except Exception:
        return None
    return register_extracted_image(content, name, book_dir, image_map)


_NARRATIVE_CHARS = re.compile(
    r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]"
)
_EPUB_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_S_BLOCK = re.compile(r"<s\b[^>]*>.*?</s>", re.I | re.S)
_LOADING_ATTR = re.compile(r"""\bloading\s*=\s*(["']?)[A-Za-z]*\1""", re.I)


# True if page HTML has readable text beyond just images and scene breaks
def html_has_narrative_besides_media(markup: str) -> bool:
    """True when leftover text remains after stripping images (mixed/bad EPUB page)."""
    if not markup:
        return False
    stripped = re.sub(
        r"<(?:svg|picture)\b[^>]*>.*?</(?:svg|picture)>", " ", markup, flags=re.I | re.S
    )
    stripped = re.sub(r"<(?:img|image)\b[^>]*/?>", " ", stripped, flags=re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    leftover = re.sub(r"\s+", " ", stripped).strip()
    return bool(leftover and _NARRATIVE_CHARS.search(leftover))


# Build <img> tag HTML for an EPUB image
def epub_image_html(src: str, loading: str, extra_attrs: str = "") -> str:
    extra = f" {extra_attrs.strip()}" if extra_attrs and extra_attrs.strip() else ""
    return f'<img src="{src}" class="epub-image" loading="{loading}"{extra}>'


# Set loading= attribute on an img node
def _set_img_loading_attr(tag: str, loading: str) -> str:
    if "epub-image" not in tag:
        return tag
    if _LOADING_ATTR.search(tag):
        return _LOADING_ATTR.sub(f'loading="{loading}"', tag, count=1)
    return re.sub(r"\s*/?>$", f' loading="{loading}">', tag)


# Rewrite all img loading= attributes in a fragment
def _rewrite_epub_image_loading(fragment: str, loading: str) -> str:
    return _EPUB_IMG_TAG.sub(lambda m: _set_img_loading_attr(m.group(0), loading), fragment)


# Apply eager/lazy loading attribute across all pages based on content
def apply_image_loading(pages: List[str]) -> List[str]:
    """Standalone image HTML stays lazy. Mixed pages and <s> images become eager."""
    if not pages:
        return pages
    out = []
    for page_html in pages:
        if not page_html or "epub-image" not in page_html:
            out.append(page_html)
            continue
        page_loading = "eager" if html_has_narrative_besides_media(page_html) else "lazy"
        rewritten = _rewrite_epub_image_loading(page_html, page_loading)
        rewritten = _S_BLOCK.sub(
            lambda m: _rewrite_epub_image_loading(m.group(0), "eager"),
            rewritten,
        )
        out.append(rewritten)
    return out


# Resolve CSS class to scene-break ornament text or image src
def css_scene_break_inner(class_name, count, master_css, image_map, doc_id, book_dir, book):
    """Symbols, original CSS image, or diamond fallback for a confirmed class."""
    pattern = r"\." + re.escape(class_name) + r"\s*\{([^}]+)\}"
    matches = re.findall(pattern, master_css or "")
    for block in matches:
        block_lower = block.lower()
        if not any(keyword in block_lower for keyword in CSS_ORNAMENT_KEYWORDS):
            continue
        url = first_css_url(block)
        if url:
            filename = resolve_css_image_url(url, book, book_dir, image_map)
            if filename and book_dir:
                classified = classify_ornament_image(
                    filename, Path(book_dir) / filename, count
                )
                if classified["kind"] == "symbols":
                    return classified["text"]
                assigned_id = urllib.parse.quote(filename)
                src = f"/api/library/image/{doc_id}/{assigned_id}"
                return epub_image_html(src, "eager")
        return "◆ ◆ ◆"
    return None


# Return the outermost <s> wrapper tag for a scene break node
def scene_break_open_tag(tag) -> str:
    s_tag = "<s"
    orig_id = (tag.attributes or {}).get("id")
    data_id = (tag.attributes or {}).get("data-orig-id")
    if orig_id:
        s_tag += f' id="{html.escape(orig_id)}"'
    if data_id:
        s_tag += f' data-orig-id="{html.escape(data_id)}"'
    return s_tag + ">"


SCENE_BREAK_INNER_WRAPPERS = (
    "div", "p", "figure", "section", "span", "a", "center", "blockquote",
)


# Climb DOM to find empty ornament wrapper containing a scene break
def climb_empty_ornament_wrapper(tag):
    top_node = tag
    curr = tag.parent
    while curr and curr.tag in SCENE_BREAK_INNER_WRAPPERS:
        if curr.text(strip=True) or len(curr.css("img, image")) > 1:
            break
        top_node = curr
        curr = curr.parent
    return top_node


# Remove s_ ids from inner scene-break nodes to prevent TOC collision
def _drop_scene_break_target_ids(node) -> None:
    """Keep <s> from stealing heading/fragment ids off ornament images."""
    if node is None:
        return
    for key in ("id", "data-orig-id"):
        try:
            if (node.attributes or {}).get(key) is not None:
                del node.attrs[key]
        except Exception:
            pass


# Remove empty wrapper divs/p inside <s> scene break elements
def strip_scene_break_inner_wrappers(tree) -> bool:
    """Inside <s>, unwrap leftover containers so only symbols or one image remain."""
    modified = False
    for s_tag in tree.css("s"):
        if s_tag.parent is None:
            continue
        while True:
            wrappers = [
                child
                for child in iter_children(s_tag)
                if child.tag in SCENE_BREAK_INNER_WRAPPERS
            ]
            if not wrappers:
                break
            for wrapper in wrappers:
                if wrapper.parent is None:
                    continue
                wrapper.unwrap()
                modified = True
        media = s_tag.css_first("img, image, svg")
        if not media:
            continue
        for child in list(iter_children(s_tag, include_text=True)):
            if child.parent is None:
                continue
            if child == media or child.tag in ("img", "image", "svg"):
                continue
            if child.css_first("img, image, svg"):
                continue
            child.decompose()
            modified = True
    return modified


# Post-pass: remove stray inner tags from all scene break elements
def clean_scene_break_contents(pages: List[str]) -> List[str]:
    """Final pass: drop div/p/span spam left inside injected <s> scene breaks."""
    if not pages:
        return pages
    new_pages = []
    for page_html in pages:
        if not page_html or "<s" not in page_html:
            new_pages.append(page_html)
            continue
        tree = HTMLParser(page_html)
        if not strip_scene_break_inner_wrappers(tree):
            new_pages.append(page_html)
            continue
        target = tree.body or tree.root
        page_str = (target.html if target else tree.html) or ""
        page_str = re.sub(r">\s*\n+\s*<", "><", page_str)
        new_pages.append(page_str)
    return new_pages


# Convert elements styled with background ornament CSS classes to <s> scene breaks
def process_css_scene_breaks(
    pages, master_css, image_map=None, doc_id="", book_dir=None, book=None
):
    if not pages:
        return pages
    class_counts = defaultdict(int)

    for page_html in pages:
        tree = HTMLParser(page_html)
        for tag in tree.css("hr, div, p, span"):
            if not tag.text(strip=True) and not tag.css_first("img, image, svg"):
                classes = (tag.attributes or {}).get("data-orig-class", "").split()
                for class_name in classes:
                    class_counts[class_name] += 1

    confirmed_classes = {}
    if master_css:
        for class_name, count in class_counts.items():
            if count < 4:
                continue
            inner = css_scene_break_inner(
                class_name, count, master_css, image_map, doc_id, book_dir, book
            )
            if inner is not None:
                confirmed_classes[class_name] = inner

    new_pages = []
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
            inner = next(
                (confirmed_classes[c] for c in classes if c in confirmed_classes),
                None,
            )
            if inner is None:
                continue
            if not sandwich_allows_scene_break(blocks, i):
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


# Identify repeated ornamental images and convert them to <s> scene breaks
def process_image_scene_breaks(pages, image_map, doc_id, book_dir):
    src_prefix = f"/api/library/image/{doc_id}/"
    symbol_map = {}
    src_counts = {}

    for page_html in pages:
        tree = HTMLParser(page_html)
        for img in tree.css("img, image"):
            if find_parent(img, "s"):
                continue
            src = (img.attributes or {}).get("src", "")
            if src.startswith(src_prefix):
                src_counts[src] = src_counts.get(src, 0) + 1

    for src, count in src_counts.items():
        assigned_id = src.replace(src_prefix, "")
        filename = image_map.get(urllib.parse.unquote(assigned_id))
        if not filename:
            continue
        classified = classify_ornament_image(filename, book_dir / filename, count)
        if classified["kind"] == "symbols":
            symbol_map[src] = classified["text"]
        elif classified["kind"] == "wrap":
            symbol_map[src] = "@@S_WRAP@@"

    if not symbol_map:
        return pages

    new_pages = []
    for page_html in pages:
        if not any(src in page_html for src in symbol_map):
            new_pages.append(page_html)
            continue

        tree = HTMLParser(page_html)
        blocks = nodes_in_document_order(
            tree,
            ("h1", "h2", "h3", "h4", "h5", "h6",
             "p", "div", "span", "img", "image"),
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
        page_str = re.sub(r">\s*\n+\s*<", "><", page_str)
        new_pages.append(page_str)

    return new_pages


# Master EPUB ingestion pipeline: extract archive, clean HTML, and build pages
@router.post("/api/convert/epub")
async def convert_epub(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(400, "Not an EPUB file")
    doc_id = id
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)

    file_bytes = await file.read()
    try:
        book = NativeEpub(io.BytesIO(file_bytes))
    except Exception as e:
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(400, f"Cannot read file: {e}")

    try:
        book_lang = language_from_epub_book(book)
        if not book_lang:
            book_lang = book.language or None

        known_toc = set()
        rich_toc = {}
        master_css = ""
        
        try:
            for item in book.get_items():
                if getattr(item, 'media_type', '') == 'text/css' or item.file_name.lower().endswith('.css'):
                    try:
                        master_css += item.get_content().decode('utf-8', errors='ignore') + "\n"
                    except Exception: pass
        except Exception: pass

        try:
            if hasattr(book, "toc"):
                def walk(items):
                    if not isinstance(items, (list, tuple)): return
                    for it in items:
                        try:
                            node = it[0] if isinstance(it, (tuple,list)) and len(it)==2 else it
                            if hasattr(node, "title") and node.title:
                                clean = " ".join(str(node.title).split()).lower()
                                known_toc.add(clean)
                                href = str(getattr(node, "href", ""))
                                if href:
                                    ch = posixpath.basename(href.split("#")[0]).lower()
                                    an = href.split("#")[1].lower() if "#" in href else ""
                                    rich_toc.setdefault(ch, []).append({"title": str(node.title), "clean_title": clean, "anchor": an})
                            if isinstance(it, (tuple,list)) and len(it)==2:
                                walk(it[1])
                        except Exception: pass
                walk(book.toc)

            if len(known_toc) < 3:
                for item in book.get_items_of_type(ITEM_DOCUMENT):
                    name_lower = item.get_name().lower()
                    if any(x in name_lower for x in ['toc', 'nav', 'tableofcontents', 'contents']):
                        try:
                            toc_tree = HTMLParser(item.get_content().decode('utf-8', 'ignore'))
                            for a_tag in toc_tree.css('a'):
                                title = a_tag.text(separator=" ", strip=True)
                                clean_title = " ".join(title.split()).lower()
                                if clean_title and len(clean_title) > 2 and not clean_title.isdigit():
                                    known_toc.add(clean_title)
                                    href = str((a_tag.attributes or {}).get('href', ''))
                                    if href:
                                        clean_href = posixpath.basename(href.split('#')[0]).lower()
                                        anchor = href.split('#')[1].lower() if '#' in href else ''
                                        rich_toc.setdefault(clean_href, []).append({
                                            'title': title, 'clean_title': clean_title, 'anchor': anchor
                                        })
                        except Exception: pass
        except Exception:
            pass

        pages, image_map, extracted = [], {}, set()
        global_idx = 0
        href_to_page = {}
        spine = getattr(book, "spine", [])

        for idx, (item_id, *_) in enumerate(spine):
            item = book.get_item_with_id(item_id)
            if not item or item.get_type() != ITEM_DOCUMENT: continue
            actual_href = item.get_name()
            raw = item.get_content().decode("utf-8", "ignore")
            
            try: raw = pre_parse_clean(raw)
            except Exception: pass

            tree = HTMLParser(raw)

            try: standardize_footnotes(tree)
            except Exception: pass

            force_formatting_markers(tree, actual_href)

            next_has_header = False
            for lookahead in range(1, 4):
                if idx + lookahead < len(spine):
                    next_item_id = spine[idx + lookahead][0]
                    next_item = book.get_item_with_id(next_item_id)
                    if next_item and next_item.get_type() == ITEM_DOCUMENT:
                        next_raw = next_item.get_content().decode("utf-8", "ignore")
                        if "<h1" in next_raw.lower() or "<h2" in next_raw.lower():
                            next_has_header = True; break

            try:
                normalize_epub_html(
                    tree=tree, 
                    known_toc_titles=known_toc, 
                    current_href=actual_href, 
                    rich_toc_map=rich_toc, 
                    next_has_header=next_has_header
                )
            except Exception: pass

            force_formatting_markers(tree, actual_href)
            html_dir = posixpath.dirname(actual_href)

            chapter_root = tree.body or tree.root
            chapter_mixed = html_has_narrative_besides_media(
                (chapter_root.html if chapter_root is not None else tree.html) or ""
            )
            img_loading = "eager" if chapter_mixed else "lazy"

            for image in list(tree.css("img, image")):
                if image.parent is None:
                    continue

                image_attrs = image.attributes or {}
                src = image_attrs.get("src") or image_attrs.get("xlink:href") or image_attrs.get("href")
                if not src:
                    svg_wrapper = find_parent(image, "svg")
                    if svg_wrapper:
                        svg_wrapper.decompose()
                    else:
                        image.decompose()
                    continue

                src = src.split("#")[0]
                resolved_href = urllib.parse.unquote(
                    posixpath.normpath(posixpath.join(html_dir, src))
                ).lstrip("/")

                image_item = book.get_item_with_href(resolved_href)
                image_content = None
                actual_item_name = None

                if image_item:
                    try:
                        image_content = image_item.get_content()
                        actual_item_name = image_item.get_name()
                    except Exception:
                        image_content = None

                if not image_content:
                    rescued = book.read_unmanifested_file(resolved_href)
                    if rescued:
                        image_content = rescued
                        actual_item_name = actual_item_name or resolved_href

                if image_content and actual_item_name:
                    clean_name = actual_item_name.split("?")[0].split("#")[0]
                    base_name = posixpath.splitext(clean_name)[0]
                    extension = posixpath.splitext(clean_name)[1].lower()

                    safe_base = re.sub(
                        r'[\\/*?:"<>|]',
                        "",
                        base_name.replace("/", "_").replace("\\", "_"),
                    )
                    if len(safe_base) > 50:
                        safe_base = safe_base[:40] + "_" + uuid.uuid4().hex[:6]
                    elif not safe_base:
                        safe_base = f"img_{uuid.uuid4().hex[:8]}"

                    if extension not in [".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"]:
                        extension = ".jpg"
                    safe_filename = f"{safe_base}{extension}"

                    if actual_item_name not in extracted:
                        image_path = book_dir / safe_filename
                        try:
                            with open(image_path, "wb") as image_file:
                                image_file.write(image_content)
                            image_map[safe_filename] = safe_filename
                            extracted.add(actual_item_name)
                        except Exception as error:
                            print(f"[Warning] Failed to save image {safe_filename} to disk: {error}")

                    assigned_id = urllib.parse.quote(safe_filename)
                    replacement = epub_image_html(
                        f"/api/library/image/{doc_id}/{assigned_id}", img_loading
                    )
                    svg_wrapper = find_parent(image, "svg")
                    replace_with_html(svg_wrapper or image, replacement)
                else:
                    svg_wrapper = find_parent(image, "svg")
                    if svg_wrapper:
                        svg_wrapper.decompose()
                    else:
                        image.decompose()

            for paragraph in list(tree.css("p, div")):
                if paragraph.parent is None or paragraph.css_first("img"):
                    continue
                paragraph_text = paragraph.text(strip=True)
                chars = [char for char in paragraph_text if not char.isspace()]
                if not chars:
                    continue

                length = len(chars)
                if length > 20:
                    continue

                if re.search(r"[a-zA-Z0-9\u00C0-\u00FF\u0400-\u04FF\u3041-\u3096\u30A1-\u30FA\u4E00-\u9FAF\uAC00-\uD7AF]", paragraph_text):
                    continue

                forbidden_punctuation = set(".,!?:;\"'“”‘’「」『』()[]{}<>。、・？！…")
                if any(char in forbidden_punctuation for char in chars):
                    continue

                is_scene_break = False
                if length >= 2:
                    is_scene_break = True
                elif length == 1:
                    valid_singles = set("*#-_~♦◇◆○●■□▼▽★☆❖✦⁂※†—–―─●")
                    if chars[0] in valid_singles:
                        is_scene_break = True

                if is_scene_break:
                    replace_with_html(paragraph, f"<s>{html.escape(paragraph_text)}</s>")

            global_idx = assign_epub_block_ids(tree)

            for block in list(tree.css("div, p, figure, span")):
                has_preserved_child = bool(
                    nodes_in_document_order(
                        block, ("img", "hr", "br", "svg", "picture", "s")
                    )
                )
                if block.parent is not None and not block.text(strip=True) and not has_preserved_child:
                    block.decompose()

            target = tree.body or tree.root
            chapter_lang = (
                language_from_html_markup((target.html if target else tree.html) or "")
                or book_lang
            )
            if chapter_lang and target is not None and not (target.attributes or {}).get("lang"):
                try:
                    target.attrs["lang"] = chapter_lang
                except Exception:
                    pass
            if chapter_lang and not book_lang:
                book_lang = chapter_lang
            page_html = (target.html if target else tree.html) or ""
            page_html = re.sub(r">\s*\n+\s*<", "><", page_html)
            page_html = restore_inline_markers(page_html)

            has_reader_content = (
                re.search(r'id=["\']s_\d+', page_html)
                or "<img" in page_html
                or "<s>" in page_html
            )
            if not has_reader_content:
                residual_tree = HTMLParser(page_html)
                residual_target = residual_tree.body or residual_tree.root
                residual_text = (
                    residual_target.text(separator=" ", strip=True)
                    if residual_target is not None
                    else ""
                )
                if residual_text:
                    # Preserve the source DOM instead of flattening and escaping
                    # an unrecognized publisher structure.
                    has_reader_content = True

            if has_reader_content:
                href_to_page[actual_href] = len(pages)
                pages.append(page_html)

        for i in range(len(pages)):
            if '<h1' in pages[i] and ('<img' in pages[i] or '<image' in pages[i] or 'epub-image' in pages[i]):
                current_tree = HTMLParser(pages[i])
                modified = False
                for h in current_tree.css('h1'):
                    img = h.css_first('img, image')
                    if img:
                        text_nodes = h.text(strip=True)
                        hidden = h.css_first('span.epub-visually-hidden')
                        hidden_text = hidden.text(strip=True) if hidden else ""
                        
                        is_pure_image = not text_nodes or (hidden_text and text_nodes == hidden_text)
                        
                        if is_pure_image:
                            found_duplicate = False
                            for lookahead in range(1, 4):
                                if i + lookahead < len(pages):
                                    next_tree = HTMLParser(pages[i + lookahead])
                                    real_headers = [
                                        nx.text(strip=True)
                                        for nx in nodes_in_document_order(
                                            next_tree, ("h1", "h2", "h3")
                                        )
                                        if nx.text(strip=True)
                                    ]
                                    
                                    if real_headers:
                                        for rh in real_headers:
                                            rh_lower = re.sub(r'[^\w\s]', '', rh.lower()).strip()
                                            if hidden_text:
                                                hidden_lower = re.sub(r'[^\w\s]', '', hidden_text.lower()).strip()
                                                if hidden_lower in rh_lower or rh_lower in hidden_lower:
                                                    found_duplicate = True; break
                                            if any(toc_title in rh_lower or rh_lower in toc_title for toc_title in known_toc if len(toc_title) > 3):
                                                found_duplicate = True; break
                                        if found_duplicate: break
                                        
                                    page_text = next_tree.text(strip=True)
                                    if len(page_text) > 20: break 
                            
                            if found_duplicate:
                                if hidden: hidden.decompose()
                                h_id = (h.attributes or {}).get('id', '')
                                replace_with_html(
                                    h, f"<p id='{h_id}'>{get_inner_html(h)}</p>"
                                )
                                modified = True
                
                if modified:
                    target = current_tree.body or current_tree.root
                    page_str = (target.html if target else current_tree.html) or ""
                    pages[i] = re.sub(r'>\s*\n+\s*<', '><', page_str)

        pages = process_css_scene_breaks(
            pages, master_css, image_map, doc_id, book_dir, book
        )
        pages = process_image_scene_breaks(pages, image_map, doc_id, book_dir)
        pages = clean_scene_break_contents(pages)
        pages = apply_image_loading(pages)

        toc = []
        try:
            if hasattr(book, "toc") and book.toc:
                def parse_toc(items, level=1):
                    res = []
                    if not items: return res
                    for item in items:
                        try:
                            if isinstance(item, (tuple, list)):
                                if len(item) == 2 and hasattr(item[0], 'title'):
                                    sec, ch = item[0], item[1]
                                    href = str(getattr(sec, "href", "") or "")
                                    chref = href.split("#")[0]
                                    anchor_id = href.split("#")[1] if "#" in href else None
                                    
                                    pidx = href_to_page.get(chref, -1)
                                    if pidx == -1:
                                        for h, p in href_to_page.items():
                                            if posixpath.basename(h) == posixpath.basename(chref):
                                                pidx = p; break
                                                
                                    if pidx != -1:
                                        title_str = str(getattr(sec, 'title') or f"Chapter (Page {pidx + 1})")
                                        res.append({"title": title_str, "level": level, "page_index": pidx, "anchor_id": anchor_id})
                                        
                                    res.extend(parse_toc(ch, level + 1))
                                else:
                                    res.extend(parse_toc(item, level))
                                    
                            elif hasattr(item, 'title') and hasattr(item, 'href'):
                                href = str(getattr(item, "href", "") or "")
                                chref = href.split("#")[0]
                                anchor_id = href.split("#")[1] if "#" in href else None
                                
                                pidx = href_to_page.get(chref, -1)
                                if pidx == -1:
                                    for h, p in href_to_page.items():
                                        if posixpath.basename(h) == posixpath.basename(chref):
                                            pidx = p; break
                                            
                                if pidx != -1:
                                    title_str = str(getattr(item, 'title') or f"Chapter (Page {pidx + 1})")
                                    res.append({"title": title_str, "level": level, "page_index": pidx, "anchor_id": anchor_id})
                        except Exception: continue
                    return res
                toc = parse_toc(book.toc)
        except Exception:
            pass

        if not toc:
            toc = generate_toc(pages)

        assign_toc_target_ids(pages, toc)

        for i in range(len(pages)):
            pages[i] = re.sub(r'\s*data-orig-id="[^"]*"', '', pages[i])
            pages[i] = re.sub(r"\s*data-orig-id='[^']*'", '', pages[i])

        if not book_lang:
            book_lang = language_from_pages(pages)
        if not book_lang:
            try:
                sample = HTMLParser("".join(pages[:3])).text(separator=" ", strip=True)
            except Exception:
                sample = ""
            book_lang = language_from_text_heuristic(sample)

        return {
            "pages": pages,
            "image_map": image_map,
            "toc_map": toc,
            "language": book_lang,
            "bookType": "epub",
        }

    except Exception as e:
        import traceback
        print("\n" + "="*60)
        print("🚨 FATAL EPUB EXTRACTION CRASH 🚨")
        traceback.print_exc()
        print("="*60 + "\n")
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(500, str(e))
    finally:
        book.close()


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
    from logic.html_normalizer import generate_toc

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
                        page_html += epub_image_html(
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
        
        if not toc_map: toc_map = generate_toc(pages)

        assign_toc_target_ids(pages, toc_map)
        pages = apply_image_loading(pages)

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


# Read and return library inventory list
@router.get("/api/library")
def get_library():
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            library = json.load(f)
    except Exception:
        return []
    if not isinstance(library, list):
        return []
    return library

# Add or update book metadata item in library.json
@router.post("/api/library")
async def save_library_item(item: LibraryItem):
    async with _library_lock:
        try:
            with open(library_file, "r", encoding="utf-8") as f:
                library = json.load(f)
        except Exception: library = []

        incoming = item.model_dump()
        incoming["bookType"] = _normalize_book_type(incoming.get("bookType"))
        found = False
        for i, existing in enumerate(library):
            if existing.get("id") == item.id:
                merged = {**existing, **incoming}
                if not merged.get("bookType") and existing.get("bookType"):
                    merged["bookType"] = _normalize_book_type(existing.get("bookType"))
                if not merged.get("language") and existing.get("language"):
                    merged["language"] = existing.get("language")
                library[i] = merged
                found = True
                break
        if not found:
            library.append(incoming)

        safe_save_json(library_file, library)
        return {"status": "ok"}

# Delete book from library inventory and remove extracted files
@router.delete("/api/library/{doc_id}")
async def delete_library_item(doc_id: str):
    async with _library_lock:
        try:
            with open(library_file, "r", encoding="utf-8") as f:
                library = json.load(f)

            len_before = len(library)
            library = [item for item in library if item.get("id") != doc_id]

            if len(library) < len_before:
                safe_save_json(library_file, library)
                book_dir = content_dir / doc_id
                if book_dir.exists(): shutil.rmtree(book_dir, ignore_errors=True)
                for ext in [".json", ".pdf", ".epub"]:
                    file_path = content_dir / f"{doc_id}{ext}"
                    if file_path.exists():
                        try: file_path.unlink()
                        except Exception: pass
                return {"status": "deleted"}
            else: raise HTTPException(status_code=404, detail="Document not found")
        except Exception as e: raise HTTPException(status_code=500, detail=str(e))

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

_SEARCH_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "section", "article", "header", "footer", "ul", "ol", "table",
    "thead", "tbody", "tfoot", "pre", "hr", "dd", "dt", "figure", "figcaption",
    "n", "nav", "aside", "main",
}

# Inline formatting: concatenate so ``the ne<em>xt</em> day`` stays ``the next day``.
_SEARCH_GLUE_TAGS = {
    "em", "i", "b", "strong", "u", "s", "strike", "del", "ins", "mark",
    "small", "big", "sub", "sup", "span", "font", "a", "abbr", "cite",
    "q", "code", "dfn", "ruby", "rt", "rp",
}


# Strip HTML tags from page for plain-text search
def html_to_search_text(page_html: str) -> str:
    """Plain text for search: keep real word spaces, do not split words at inline tags.

    selectolax ``text(separator=" ")`` joins every node with a space, which
    turns ``the ne<em>xt</em> day`` into ``the ne xt day``.
    """
    tree = HTMLParser(page_html)
    target = tree.body if tree.body else tree
    if target is None:
        return ""
    parts = []
    only_glue_since_text = True
    crossed_block = False

    def append_text(text: str) -> None:
        nonlocal only_glue_since_text, crossed_block
        if not text:
            return
        if parts:
            prev = parts[-1]
            has_ws = prev[-1].isspace() or text[0].isspace()
            if not has_ws and (crossed_block or not only_glue_since_text):
                parts.append(" ")
        parts.append(text)
        only_glue_since_text = True
        crossed_block = False

    def walk(node):
        nonlocal only_glue_since_text, crossed_block
        child = node.child
        while child is not None:
            nxt = child.next
            tag = child.tag
            if tag == "-text":
                append_text(child.text(strip=False) or "")
            elif tag not in ("script", "style", "rt", "rp"):
                is_block = tag in _SEARCH_BLOCK_TAGS
                is_glue = tag in _SEARCH_GLUE_TAGS
                if is_block:
                    crossed_block = True
                elif not is_glue:
                    only_glue_since_text = False
                walk(child)
                if is_block:
                    crossed_block = True
                elif not is_glue:
                    only_glue_since_text = False
            child = nxt

    walk(target)
    return "".join(parts)


# Escape and normalize quote variants in a search query
def escape_search_query(q_norm: str) -> str:
    """Escape each word, then join with ``\\s+``.

    Do not ``re.escape`` the whole phrase then replace spaces: Python 3.13
    escapes spaces as ``\\ ``, and a follow-up ``\\s+`` swap leaves a stray
    backslash so two-word queries never match.
    """
    tokens = [token for token in re.split(r"\s+", q_norm) if token]
    escaped_tokens = [
        re.escape(token).replace("'", r"['‘’´`]").replace('"', r'["“”]')
        for token in tokens
    ]
    return r"\s+".join(escaped_tokens)


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
    escaped_q = escape_search_query(q_norm)
    if not escaped_q:
        return {"results": [], "total_matches": 0, "query": q}
    pattern_str = rf"\b{escaped_q}\b" if whole_word else escaped_q
    
    try: pattern = re.compile(pattern_str, flags)
    except Exception: return {"results": [], "total_matches": 0, "query": q}

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

# Save current reading position and progress for a book
@router.post("/api/library/progress/{doc_id}")
async def update_book_progress_checkpoint(doc_id: str, payload: ProgressUpdatePayload):
    if not library_file.exists(): raise HTTPException(status_code=404, detail="Library inventory log absent.")
    async with _library_lock:
        try:
            with open(library_file, "r", encoding="utf-8") as f: books_inventory = json.load(f)
            target_book = next((book for book in books_inventory if book.get("id") == doc_id), None)
            if not target_book: raise HTTPException(status_code=404, detail="Requested record entry missing.")

            target_book["currentPage"] = payload.currentPage
            target_book["lastSentenceId"] = payload.lastSentenceId
            target_book["lastSentenceIndex"] = payload.lastSentenceIndex
            target_book["lastAccessed"] = payload.lastAccessed
            if payload.current_page is not None:
                target_book["current_page"] = payload.current_page
            if payload.total_pages is not None:
                target_book["total_pages"] = payload.total_pages
            if payload.progress_percent is not None:
                target_book["progress_percent"] = payload.progress_percent

            temp_lib_path = library_file.with_suffix(".tmp")
            with open(temp_lib_path, "w", encoding="utf-8") as write_handle:
                json.dump(books_inventory, write_handle, indent=4, ensure_ascii=False)
            temp_lib_path.replace(library_file)
            
        except Exception as io_error:
            print(f"[Error] Failed to auto-save progress to library.json: {io_error}")
            raise HTTPException(status_code=500, detail=f"Database sync failure: {str(io_error)}")

    return {"status": "success", "message": f"Checkpoint saved for {doc_id}"}