"""EPUB book map. TOC entries and the s_* ids the reader jumps to.

No FastAPI, no peeker, and no tag cleanup.
"""
import html
import posixpath
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .html_normalizer import (
    _class_list,
    _get_attr,
    _nodes_in_document_order,
    get_inner_html,
    nodes_in_document_order,
    replace_with_html,
    safe_unwrap,
)



# Serialize attribute dict to an HTML attribute string
def _attrs_to_str(attrs) -> str:
    if not attrs:
        return ""
    return "".join(
        f' {key}="{html.escape(str(value), quote=True)}"'
        for key, value in attrs.items() if value is not None
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



# Build TOC list from h1/h2/h3 headings found across all processed pages
def generate_toc(pages: List[str]) -> List[Dict[str, Any]]:
    toc: List[Dict[str, Any]] = []
    junk = re.compile(r"^[\W_]+$")

    for page_index, page_html in enumerate(pages):
        tree = HTMLParser(page_html)
        body = tree.body or tree.root
        if body is None:
            continue
        for heading in _nodes_in_document_order(
            body, ("h1", "h2", "h3", "h4", "h5", "h6")
        ):
            title = heading.text(strip=True)
            if not title or len(title) >= 150 or junk.match(title):
                continue
            if any(
                item["page_index"] == page_index and item["title"] == title
                for item in toc
            ):
                continue
            toc.append(
                {
                    "title": title,
                    "level": int(heading.tag[1]),
                    "page_index": page_index,
                    "anchor_id": _get_attr(heading, "id") or None,
                }
            )

    if not toc:
        semantic = ("chapter", "chap", "title", "heading", "h1", "h2", "h3")
        for page_index, page_html in enumerate(pages):
            tree = HTMLParser(page_html)
            body = tree.body or tree.root
            if body is None:
                continue
            for element in _nodes_in_document_order(body, ("p", "div", "span")):
                classes = _class_list(element)
                if not classes or not any(
                    marker in class_name.lower()
                    for class_name in classes
                    for marker in semantic
                ):
                    continue
                title = element.text(strip=True)
                if title and len(title) < 150 and not junk.match(title):
                    toc.append(
                        {
                            "title": title,
                            "level": 1,
                            "page_index": page_index,
                            "anchor_id": _get_attr(element, "id") or None,
                        }
                    )
                    break

    if not toc:
        fallback = re.compile(
            r"^(chapter|prologue|epilogue|part|volume|interlude)\b|"
            r"^act\s*[\dIVXLCDM]+",
            re.IGNORECASE,
        )
        for page_index, page_html in enumerate(pages):
            tree = HTMLParser(page_html)
            body = tree.body or tree.root
            if body is None:
                continue
            checked = 0
            for element in _nodes_in_document_order(body, ("p", "div")):
                title = element.text(strip=True)
                if not title or junk.match(title):
                    continue
                checked += 1
                if len(title) < 100 and fallback.match(title):
                    toc.append(
                        {
                            "title": title,
                            "level": 1,
                            "page_index": page_index,
                            "anchor_id": _get_attr(element, "id") or None,
                        }
                    )
                    break
                if checked >= 2:
                    break

    if toc:
        levels = sorted({item["level"] for item in toc})
        level_map = {level: index + 1 for index, level in enumerate(levels)}
        for item in toc:
            item["level"] = level_map[item["level"]]

    if len(toc) > 2:
        duplicate_levels = sum(
            toc[index]["page_index"] == toc[index - 1]["page_index"]
            and toc[index]["level"] == toc[index - 1]["level"]
            for index in range(1, len(toc))
        )
        if duplicate_levels / len(toc) >= 0.25:
            for index in range(1, len(toc)):
                if (
                    toc[index]["page_index"] == toc[index - 1]["page_index"]
                    and toc[index]["level"] == toc[index - 1]["level"]
                ):
                    toc[index]["level"] += 1

    return sorted(toc, key=lambda item: item["page_index"])


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


def parse_epub_toc(manifest: Dict[str, Any], raw: str = "") -> List[Dict[str, Any]]:
    """Map NCX or nav markup onto spine page indexes.

    The caller supplies the TOC file text. This function does not open the EPUB.
    """
    toc_file = manifest.get("toc_file", "")
    spine = manifest.get("spine", [])
    toc: List[Dict[str, Any]] = []

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
