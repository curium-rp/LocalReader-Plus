import html
import posixpath
import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Set

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

try:
    from utils import normalize_bcp47
except ImportError:
    try:
        from ..utils import normalize_bcp47
    except ImportError:
        def normalize_bcp47(value, default=None):
            raw = str(value or "").strip().replace("_", "-")
            primary = raw.split("-")[0].lower()
            return primary or default


BLOCK_TAGS = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6")
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
PROTECTED_CLASSES = {
    "epub-footnote",
    "epub-noteref",
    "epub-backlink",
    "epub-image",
    "epub-visually-hidden",
    "pagebreak",
    "page-break",
}


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


# Return serialized inner HTML string of a node
def get_inner_html(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return node.inner_html or ""


# Return node attributes dict, empty if None
def _attrs(node: Optional[Node]) -> Dict[str, str]:
    return dict(node.attributes or {}) if node is not None else {}


# Read a single attribute with fallback default
def _get_attr(node: Optional[Node], key: str, default: str = "") -> str:
    if node is None:
        return default
    value = (node.attributes or {}).get(key, default)
    return str(value) if value is not None else default


# Write a single attribute onto a node
def _set_attr(node: Optional[Node], key: str, value: str) -> None:
    if node is not None:
        node.attrs[key] = value


# Remove an attribute from a node
def _del_attr(node: Optional[Node], key: str) -> None:
    if node is not None and key in (node.attributes or {}):
        del node.attrs[key]


# Return list of class tokens from a node
def _class_list(node: Optional[Node]) -> List[str]:
    return _get_attr(node, "class").split()


# Read lang/xml:lang from node, empty string if absent
def _raw_lang_attr(node: Optional[Node]) -> str:
    if node is None:
        return ""
    attrs = node.attributes or {}
    preferred = ""
    xml_lang = ""
    for key, value in attrs.items():
        if not value:
            continue
        lower = key.lower()
        if lower == "lang" and not preferred:
            preferred = str(value)
        elif lower == "xml:lang" and not xml_lang:
            xml_lang = str(value)
    return preferred or xml_lang


def _nodes_in_document_order(root: Any, tags: Iterable[str]) -> List[Node]:
    """Match BeautifulSoup.find_all(tag_list) ordering with Selectolax."""
    wanted = set(tags)
    base = (root.body or root.root) if isinstance(root, HTMLParser) else root
    if base is None:
        return []
    ordered: List[Node] = []

    def visit(parent: Node) -> None:
        child = parent.child
        while child is not None:
            next_child = child.next
            if child.tag in wanted:
                ordered.append(child)
            visit(child)
            child = next_child

    visit(base)
    return ordered


# Serialize attribute dict to an HTML attribute string
def _attrs_html(attrs: Dict[str, str]) -> str:
    return "".join(
        f' {html.escape(str(key), quote=True)}="{html.escape(str(value), quote=True)}"'
        for key, value in attrs.items()
        if value is not None
    )


def _replace_with_html(node: Optional[Node], markup: str) -> None:
    """Replace a node with parsed markup instead of escaped text."""
    if node is None or node.parent is None:
        return
    destination_parent = node.parent
    marker = "data-selectolax-replacement-root"
    fragment = HTMLParser(f"<body><div {marker}>{markup}</div></body>")
    wrapper = fragment.css_first(f"[{marker}]")
    if wrapper is None:
        node.decompose()
        return
    node.replace_with(wrapper)
    # Reacquire after moving across parser trees; the source handle is stale.
    moved_wrapper = destination_parent.css_first(f"[{marker}]")
    if moved_wrapper is not None:
        moved_wrapper.unwrap()


# Rename a tag while preserving inner HTML and optional new inner HTML
def _replace_tag(
    node: Optional[Node],
    new_tag: str,
    *,
    inner_html: Optional[str] = None,
    attrs: Optional[Dict[str, str]] = None,
) -> None:
    if node is None or node.parent is None:
        return
    content = get_inner_html(node) if inner_html is None else inner_html
    node_attrs = _attrs(node) if attrs is None else attrs
    _replace_with_html(
        node, f"<{new_tag}{_attrs_html(node_attrs)}>{content}</{new_tag}>"
    )


# Unwrap a node lifting children into parent (safe guard)
def safe_unwrap(node: Optional[Node]) -> None:
    if node is None or node.parent is None:
        return
    if node.tag in ("html", "head", "body", None):
        return
    node.unwrap()


# Walk ancestors to find first matching tag
def _find_parent(node: Optional[Node], tags: Iterable[str]) -> Optional[Node]:
    wanted = set(tags)
    parent = node.parent if node is not None else None
    while parent is not None:
        if parent.tag in wanted:
            return parent
        if parent.tag in ("html", "body", None):
            break
        parent = parent.parent
    return None


# True if any ancestor carries a given attribute value
def _has_parent_attr(node: Node, key: str, value: str) -> bool:
    parent = node.parent
    while parent is not None:
        if _get_attr(parent, key).lower() == value.lower():
            return True
        parent = parent.parent
    return False


# Find first node in tree matching attribute key=value
def _find_by_attr(tree: HTMLParser, key: str, value: str) -> Optional[Node]:
    for node in tree.css(f"[{key}]"):
        if _get_attr(node, key) == value:
            return node
    return None


# True if node has style/class hinting at bold/italic/underline/del
def _has_formatting_hint(node: Node) -> bool:
    style = _get_attr(node, "style").lower()
    classes = _get_attr(node, "class").lower()
    return any(
        hint in style or hint in classes
        for hint in (
            "bold",
            "bld",
            "strong",
            "italic",
            "oblique",
            "emphasis",
            "underline",
            "strike",
            "line-through",
            "font-weight",
            "font-style",
            "text-decoration",
        )
    )


def parse_prefix_num_suffix(s: str) -> tuple[str, str, str]:
    """Split any identifier into (prefix, number, suffix) with zero keywords."""
    m = re.match(r"^([a-zA-Z_\-]*?)(\d+)([a-zA-Z_\-]*)$", s)
    if m:
        return m.group(1).lower(), m.group(2), m.group(3).lower()
    return s.lower(), "", ""


def footnote_number_from_id(block_id: str) -> str:
    """Pull the visible note index out of ids like cite_note-3, sdfootnote1sym, TN1."""
    if not block_id:
        return ""
    _, num, _ = parse_prefix_num_suffix(block_id)
    return num


def standardize_footnotes(tree: HTMLParser) -> None:
    """Pure-logic footnote standardizer with zero hardcoded keywords or pattern lists.

    Applies the three universal footnote graph topologies:
      - Topology 1 (Direct Target): Callout points directly to an element id or name.
      - Topology 2 (Mutual 2-Cycle Graph): Callout A -> B, and definition contains backlink B -> A.
      - Topology 3 (Suffix Symmetry): Callout and definition anchors share (prefix, number)
        with differing suffixes (e.g. sym vs anc) when exporter stripped id attributes.
    """
    callouts = []
    for a in list(tree.css("a")):
        href = _get_attr(a, "href")
        if not href or "#" not in href:
            continue

        if href.startswith(("http://", "https://", "mailto:")):
            continue

        # Skip anchors inside existing footnote definition blocks
        in_footnote = (
            _has_parent_attr(a, "epub:type", "footnote")
            or _has_parent_attr(a, "epub:type", "endnote")
            or _has_parent_attr(a, "role", "doc-footnote")
            or _has_parent_attr(a, "role", "doc-endnote")
            or _find_parent(a, ("aside", "dd")) is not None
        )
        if in_footnote:
            continue

        sup = _find_parent(a, ("sup",)) or a.css_first("sup")
        epub_type = _get_attr(a, "epub:type").lower()
        role = _get_attr(a, "role").lower()
        classes = set(_class_list(a))

        is_callout = (
            sup is not None
            or epub_type == "noteref"
            or role == "doc-noteref"
            or bool(classes & {"noteref", "footnote-ref", "epub-noteref"})
        )

        if is_callout:
            frag = href.split("#")[-1].strip()
            cid = _get_attr(a, "id") or _get_attr(a, "name") or (_get_attr(sup, "id") if sup else "")
            callouts.append((a, sup, frag, cid))

    for a, sup, tfrag, cid in callouts:
        matched_block = None
        matched_backlink = None

        # Topology 1: Direct target in same tree
        if tfrag:
            direct_el = tree.css_first(f'[id="{tfrag}"], [name="{tfrag}"]')
            if direct_el:
                matched_block = _find_parent(direct_el, ("li", "p", "aside", "div", "section", "dd")) or direct_el
                for a_back in matched_block.css("a"):
                    if "#" in _get_attr(a_back, "href"):
                        matched_backlink = a_back
                        break

        # Topology 2: Mutual 2-cycle in same tree
        if not matched_block and cid:
            for a_back in tree.css("a"):
                bhref = _get_attr(a_back, "href")
                if bhref == f"#{cid}":
                    matched_block = _find_parent(a_back, ("li", "p", "aside", "div", "section", "dd")) or a_back
                    matched_backlink = a_back
                    if not _get_attr(matched_block, "id"):
                        _set_attr(matched_block, "id", tfrag)
                    break

        # Topology 3: Suffix symmetry in same tree (broken exporter without ids)
        if not matched_block and tfrag:
            c_pref, c_num, c_suf = parse_prefix_num_suffix(tfrag)
            if c_num:
                for a_cand in tree.css("a"):
                    cand_href = _get_attr(a_cand, "href")
                    if cand_href.startswith("#"):
                        cand_frag = cand_href[1:].strip()
                        k_pref, k_num, k_suf = parse_prefix_num_suffix(cand_frag)
                        if k_pref == c_pref and k_num == c_num and k_suf != c_suf:
                            matched_block = _find_parent(a_cand, ("li", "p", "aside", "div", "section", "dd")) or a_cand
                            matched_backlink = a_cand
                            _set_attr(matched_block, "id", tfrag)
                            break

        if matched_block:
            _set_attr(a, "epub:type", "noteref")
            _set_attr(matched_block, "epub:type", "footnote")
            if matched_backlink:
                _set_attr(matched_backlink, "epub:type", "backlink")


def pre_parse_clean(html_string: str) -> str:
    html_string = re.sub(
        r"<\?xml.*?\?>", "", html_string, flags=re.IGNORECASE | re.DOTALL
    )
    html_string = re.sub(
        r"<!DOCTYPE.*?>", "", html_string, flags=re.IGNORECASE | re.DOTALL
    )
    # XHTML permits self-closing non-void elements. HTML5 parsers treat raw-text
    # forms such as <title/> as opening tags and consume the remaining chapter.
    html_string = re.sub(
        r"<(head|title|textarea|script|style|xmp|iframe|noembed|noframes|noscript)"
        r"(\s[^<>]*?)?\s*/>",
        lambda match: (
            f"<{match.group(1)}{match.group(2) or ''}>"
            f"</{match.group(1)}>"
        ),
        html_string,
        flags=re.IGNORECASE,
    )
    html_string = re.sub(
        r'<script[^>]*src="[^"]*kobo\.js"[^>]*>.*?</script>',
        "",
        html_string,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_string = re.sub(
        r'<style[^>]*id="koboSpanStyle"[^>]*>.*?</style>',
        "",
        html_string,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_string = re.sub(
        r'\s*class="koboSpan"', "", html_string, flags=re.IGNORECASE
    )
    html_string = re.sub(
        r'\s*id="kobo\.[^"]*"', "", html_string, flags=re.IGNORECASE
    )
    return html_string


def standardize_formatting(tree: HTMLParser) -> None:
    """Convert publisher formatting hints into stable semantic tags."""
    for selector, replacement in (
        ("strong", "b"),
        ("em", "i"),
        ("strike, s", "del"),
    ):
        for node in list(tree.css(selector)):
            _replace_tag(node, replacement)

    bold_regex = re.compile(
        r"\b(bold|bld|strong|calibre_bold|fw-bold|font-bold|b-text)\b",
        re.IGNORECASE,
    )
    ital_regex = re.compile(
        r"\b(italic|it|em|emphasis|oblique|calibre_italic|fs-italic|i-text)\b",
        re.IGNORECASE,
    )
    underline_regex = re.compile(
        r"\b(underline|u-text|calibre_under|text-decoration-underline)\b",
        re.IGNORECASE,
    )
    delete_regex = re.compile(
        r"\b(strike|strikethrough|line-through|del|text-decoration-line-through)\b",
        re.IGNORECASE,
    )

    # Re-query after tag replacement: selectolax nodes can be invalidated by mutation.
    for node in _nodes_in_document_order(
        tree, ("span", "font", "div", "p", "a", "em", "strong")
    ):
        if node.parent is None:
            continue
        style = _get_attr(node, "style").lower().replace(" ", "")
        classes = _get_attr(node, "class").lower()
        formats = (
            (
                "del",
                "text-decoration:line-through" in style
                or "text-decoration-line:line-through" in style
                or bool(delete_regex.search(classes)),
            ),
            (
                "u",
                "text-decoration:underline" in style
                or "text-decoration-line:underline" in style
                or bool(underline_regex.search(classes)),
            ),
            (
                "i",
                "font-style:italic" in style
                or "font-style:oblique" in style
                or bool(ital_regex.search(classes)),
            ),
            (
                "b",
                any(
                    value in style
                    for value in (
                        "font-weight:bold",
                        "font-weight:700",
                        "font-weight:800",
                        "font-weight:900",
                        "font-weight:bolder",
                    )
                )
                or bool(bold_regex.search(classes)),
            ),
        )
        inner = get_inner_html(node)
        if not any(enabled for _, enabled in formats):
            continue
        for tag_name, enabled in formats:
            if enabled:
                inner = f"<{tag_name}>{inner}</{tag_name}>"
        _replace_tag(node, node.tag, inner_html=inner)

    for node in list(tree.css("[style]")):
        if node.parent is None:
            continue
        style = _get_attr(node, "style").lower().replace(" ", "")
        if (
            "text-decoration:underline" in style
            or "text-decoration-line:underline" in style
        ) and node.tag not in ("u", "b", "i", "del"):
            _replace_tag(node, "u")
        elif (
            "text-decoration:line-through" in style
            or "text-decoration-line:line-through" in style
        ) and node.tag not in ("del", "b", "i", "u"):
            _replace_tag(node, "del")


# Public entry point: run the full EPUB normalization pipeline on a tree
def normalize_epub_html(
    tree: HTMLParser,
    known_toc_titles: Optional[Set[str]] = None,
    current_href: Optional[str] = None,
    rich_toc_map: Optional[Dict[str, Any]] = None,
    next_has_header: bool = False,
) -> None:
    return _normalize_epub_html(
        tree, known_toc_titles, current_href, rich_toc_map, next_has_header
    )


# Public wrapper: last-resort heading promotion from fallback patterns
def apply_super_fallback_headings(tree: HTMLParser) -> None:
    return _apply_super_fallback_headings(tree)


# Lift empty heading tags that contain only a sibling image into the heading
def promote_image_headers(tree: HTMLParser) -> None:
    for heading in _nodes_in_document_order(tree, ("h1", "h2", "h3")):
        if heading.text(strip=True) or heading.css_first("img, svg"):
            continue

        sibling = heading.next
        while sibling is not None and sibling.tag == "-text" and not sibling.text(strip=True):
            sibling = sibling.next
        if sibling is None or sibling.tag not in ("div", "p", "section"):
            continue

        image = sibling.css_first("img")
        if image is None:
            continue
        anchor = _find_parent(image, ("a",))
        movable = (
            anchor
            if anchor is not None and _find_parent(anchor, (sibling.tag,)) == sibling
            else image
        )
        image_html = movable.html or image.html or ""
        _replace_tag(heading, heading.tag, inner_html=image_html)
        if not sibling.text(strip=True):
            sibling.decompose()


# Public wrapper: inject h1 for TOC-mapped pages whose chapter marker is an image
def inject_mapped_image_headings(
    tree: HTMLParser, current_href: str, rich_toc_map: Dict[str, Any]
) -> bool:
    return _inject_mapped_image_headings(tree, current_href, rich_toc_map)


# Public wrapper: inject h1 wrapping known-TOC-title images (no TOC map fallback)
def inject_image_headings(
    tree: HTMLParser, known_toc_titles: Optional[Set[str]]
) -> None:
    return _inject_image_headings(tree, known_toc_titles)


# Strip script/style/meta/iframe/link/noscript from the tree
def exterminate_bad_tags(tree: HTMLParser) -> None:
    for node in list(tree.css("script, style, meta, iframe, link, noscript")):
        node.decompose()


def nuke_inline_toc(tree: HTMLParser) -> bool:
    """The TOC Sniper: Vaporizes pages that are just native TOC links."""
    links = list(tree.css("a"))
    if links:
        target = tree.body or tree.root
        if target is None:
            return False
        link_text_len = sum(len(link.text(strip=True)) for link in links)
        text_content = target.text(strip=True)
        text_lower = text_content.lower()
        is_toc_page = (
            "table of contents" in text_lower
            or "contents" in text_lower
            or "toc" in text_lower.split()
        )
        if (
            is_toc_page
            and len(text_content) > 0
            and link_text_len / len(text_content) > 0.4
            and len(links) > 3
        ):
            for child in list(iter_children(target, include_text=True)):
                child.decompose()
            return True
    return False


# Unwrap decorative span/font/label/etc. tags that add no semantic value
def fix_span_fragmentation(tree: HTMLParser) -> None:
    reserved_classes = {
        "epub-footnote", "epub-noteref", "pagebreak", "page-break",
        "epub-visually-hidden",
    }
    selector = (
        "span, font, label, small, big, abbr, dfn, kbd, samp, var, mark, "
        "bdi, bdo, time, data, tt, cite, q"
    )
    span_tags = tuple(part.strip() for part in selector.split(","))
    for span in _nodes_in_document_order(tree, span_tags):
        if span.parent is None:
            continue
        classes = {value.lower() for value in _class_list(span)}
        if classes & reserved_classes or _has_formatting_hint(span):
            continue
        span_id = _get_attr(span, "id")
        if span_id:
            parent = _find_parent(span, BLOCK_TAGS)
            if parent is not None and not _get_attr(parent, "id"):
                _set_attr(parent, "id", span_id)
        safe_unwrap(span)


def promote_lang_attributes(tree: HTMLParser) -> None:
    """Keep hyphenation dictionaries working: xml:lang -> lang, html lang -> body."""
    html_el = tree.css_first("html")
    body_el = tree.body
    html_lang = normalize_bcp47(_raw_lang_attr(html_el))
    body_lang = normalize_bcp47(_raw_lang_attr(body_el))
    root_lang = body_lang or html_lang
    if root_lang and body_el is not None and not body_lang:
        _set_attr(body_el, "lang", root_lang)

    for node in list(tree.css("*")):
        raw = _raw_lang_attr(node)
        tag = normalize_bcp47(raw) if raw else None
        attrs = node.attributes or {}
        for key in list(attrs.keys()):
            if key.lower() == "xml:lang":
                _del_attr(node, key)
        if tag:
            _set_attr(node, "lang", tag)
        elif _get_attr(node, "lang"):
            _del_attr(node, "lang")


# Strip presentational attributes (class, style, align, etc.) and record originals
def strip_junk_attributes(tree: HTMLParser) -> None:
    promote_lang_attributes(tree)
    junk = {
        "class", "style", "dir", "xml:lang", "align", "valign",
        "bgcolor", "color", "role", "type", "epub:type",
    }
    for node in list(tree.css("*")):
        classes = _class_list(node)
        if classes:
            _set_attr(node, "data-orig-class", " ".join(classes))

        protected_classes = [
            value for value in classes if value.lower() in PROTECTED_CLASSES
        ]
        for attribute in list((node.attributes or {}).keys()):
            if attribute.lower() not in junk:
                continue
            if attribute.lower() == "class" and protected_classes:
                _set_attr(node, "class", " ".join(protected_classes))
                continue
            if attribute.lower() == "epub:type" and any(
                value in _get_attr(node, attribute).lower()
                for value in ("noteref", "footnote", "backlink")
            ):
                continue
            _del_attr(node, attribute)



# Block-level ancestors that signal a <code> is inline dialogue/text formatting,
# not a real code snippet.  <pre> is intentionally absent: <pre><code>...</code></pre>
# is canonical code markup and must be left alone.
_INLINE_CODE_PARENT_TAGS = frozenset({
    "p", "div", "li", "blockquote", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
})


def unwrap_inline_code(tree: HTMLParser) -> None:
    """Unwrap <code> tags that wrap dialogue, names, or plain prose text.

    Publishers (especially fan-translation EPUBs) sometimes emit patterns like:

        <p><code><b>Speaker:</b></code><code> dialogue text…</code><br/><br/></p>

    These <code> elements carry no semantic meaning and interfere with text
    rendering.  This pass replaces each qualifying <code> with its inner
    children *in-place*, preserving every descendant node (<b>, <i>, <br/>,
    text nodes, etc.) exactly as authored.

    A <code> is considered "inline / dialogue" and therefore unwrappable when:
      1. Its *immediate* parent is one of the common block-level tags listed in
         _INLINE_CODE_PARENT_TAGS  (so top-level or <pre>-wrapped code is safe).
      2. It does NOT have a <pre> ancestor (belt-and-suspenders: real code blocks
         use <pre><code>…</code></pre>).
      3. It contains no nested <code>, <pre>, or <samp> descendants (i.e. it is
         not itself a structured code example).

    """
    for code in _nodes_in_document_order(tree, ("code",)):
        if code.parent is None:
            continue

        # Rule 1 – immediate parent must be a normal prose block.
        if code.parent.tag not in _INLINE_CODE_PARENT_TAGS:
            continue

        # Rule 2 – must not be inside a <pre> anywhere up the tree.
        if _find_parent(code, ("pre",)) is not None:
            continue

        # Rule 3 – must not contain nested code/pre/samp (real code indicators).
        # NOTE: selectolax's css_first() includes the element itself, so we cannot
        # use code.css_first("code, pre, samp") — it would always match.
        # _nodes_in_document_order(code, ...) visits only descendants, not self.
        if _nodes_in_document_order(code, ("code", "pre", "samp")):
            continue

        # All guards passed → unwrap: replace <code> with its children in-place.
        safe_unwrap(code)


# Clean up empty blocks, unwrap bare anchors, convert plain divs to p
def heavy_paragraph_cleanup(tree: HTMLParser) -> None:
    unwrap_inline_code(tree)

    for anchor in list(tree.css("a")):
        if anchor.parent is None:
            continue
        classes = {value.lower() for value in _class_list(anchor)}
        epub_type = _get_attr(anchor, "epub:type").lower()
        protected = any(
            value in epub_type for value in ("noteref", "footnote", "backlink")
        ) or bool(
            classes & {"epub-noteref", "epub-footnote", "epub-backlink"}
        )
        is_sup = _find_parent(anchor, ("sup",)) is not None or anchor.css_first("sup") is not None
        has_image = anchor.css_first("img, svg, picture") is not None
        if (protected or is_sup) and not has_image:
            continue

        anchor_id = _get_attr(anchor, "id") or _get_attr(anchor, "name")
        if anchor_id:
            child = anchor.css_first("h1, h2, h3, h4, h5, h6, p, div")
            parent = _find_parent(
                anchor,
                ("h1", "h2", "h3", "h4", "h5", "h6", "p"),
            )
            target = child or parent
            if target is not None and not _get_attr(target, "id"):
                _set_attr(target, "id", anchor_id)
                _set_attr(target, "data-orig-id", anchor_id)
        safe_unwrap(anchor)

    for block in _nodes_in_document_order(tree, ("p", "div")):
        if block.parent is None:
            continue
        raw_text = block.text(strip=True)
        if not raw_text and block.css_first("img, image, svg, picture, br, a") is None:
            block.decompose()
            continue
        nested = _nodes_in_document_order(
            block,
            (
                "p", "div", "ul", "ol", "table", "blockquote",
                "h1", "h2", "h3", "h4", "h5", "h6",
            ),
        )
        has_nested_block = bool(nested)
        if block.tag == "div" and raw_text and not has_nested_block:
            _replace_tag(block, "p")


# Inject h1 for TOC-mapped pages whose chapter marker is an image
def _inject_mapped_image_headings(
    tree: HTMLParser, current_href: str, rich_toc_map: Dict[str, Any]
) -> bool:
    if not current_href or not rich_toc_map or tree.css_first("h1, h2, h3"):
        return False

    clean_href = current_href.split("/")[-1].split("#")[0].lower()
    expected_nodes = rich_toc_map.get(clean_href, [])
    if not expected_nodes:
        return False

    for image in _nodes_in_document_order(tree, ("img", "image")):
        parent = _find_parent(image, ("div", "p", "section"))
        parent_id = _get_attr(parent, "id").lower()
        image_alt = _get_attr(image, "alt").lower()
        anchor = _find_parent(image, ("a",))
        anchor_href = _get_attr(anchor, "href").lower()

        for expected in expected_nodes:
            expected_anchor = str(expected.get("anchor", "")).lower()
            expected_title = str(expected.get("clean_title", "")).lower()
            matches = bool(
                expected_anchor
                and (
                    expected_anchor in parent_id
                    or expected_anchor in anchor_href
                )
            )
            if not matches:
                matches = bool(
                    expected_title
                    and len(expected_title) > 3
                    and expected_title in image_alt
                )
            if not matches:
                continue

            wrapper = _find_parent(image, ("div", "p"))
            if wrapper is None or wrapper.text(strip=True):
                continue
            inner = get_inner_html(wrapper)
            if (
                len(image_alt) < 4
                or "image" in image_alt
                or "img" in image_alt
            ):
                hidden = html.escape(str(expected.get("title", "")))
                inner = f'<span class="epub-visually-hidden">{hidden}</span>{inner}'
            _replace_tag(wrapper, "h1", inner_html=inner)
            return True
    return False


# Inject h1 wrapping known-TOC-title images (no TOC map fallback)
def _inject_image_headings(
    tree: HTMLParser, known_toc_titles: Optional[Set[str]]
) -> None:
    if tree.css_first("h1, h2, h3"):
        return

    for image in _nodes_in_document_order(tree, ("img", "image")):
        src = _get_attr(image, "src").lower()
        alt = _get_attr(image, "alt").lower()
        if any(
            label in alt
            for label in (
                "cover",
                "title page",
                "illustration",
                "insert",
                "frontispiece",
                "copyright",
            )
        ):
            continue

        filename = src.split("/")[-1].split(".")[0]
        matched = False
        for title in known_toc_titles or set():
            if len(title) < 4:
                continue
            if title == alt or title in alt:
                matched = True
                break
            for number in re.findall(r"\d+", title):
                if (
                    number in filename
                    or number.zfill(2) in filename
                    or number.zfill(3) in filename
                ) and any(
                    keyword in filename
                    for keyword in ("ch", "chap", "chapter", "part", "vol")
                ):
                    matched = True
                    break
            if matched:
                break
            clean_title = "".join(char for char in title if char.isalnum())
            clean_file = "".join(char for char in filename if char.isalnum())
            clean_alt = "".join(char for char in alt if char.isalnum())
            if clean_title and (
                clean_title in clean_file or clean_title in clean_alt
            ):
                matched = True
                break

        if not matched:
            strict = re.compile(
                r"^(chapter|prologue|epilogue|part|volume)[\s_-]*[\dIVX]+$",
                re.IGNORECASE,
            )
            matched = bool(strict.match(filename) or strict.match(alt))

        if matched:
            wrapper = _find_parent(image, ("div", "p"))
            if wrapper is not None and not wrapper.text(strip=True):
                _replace_tag(wrapper, "h1")
                return


# Last-resort: promote first short block matching chapter regex to h1
def _apply_super_fallback_headings(
    tree: HTMLParser, known_toc_titles: Optional[Set[str]] = None
) -> None:
    heading_pattern = re.compile(
        r"^(chapter\s*[\dIVXLCDM]+|prologue|epilogue|part\s*[\dIVXLCDM]+|"
        r"volume\s*[\dIVXLCDM]+)(?:[\s:,\-].*)?$",
        re.IGNORECASE,
    )
    h1_keywords = re.compile(
        r"^(prologue|epilogue|part\b|volume\b|book\b)", re.IGNORECASE
    )

    for index, block in enumerate(_nodes_in_document_order(tree, ("p", "div"))):
        if index >= 20:
            break
        raw_text = " ".join(block.text(separator=" ", strip=True).split())
        if not raw_text or len(raw_text) > 120:
            continue

        heading_level = ""
        text_lower = raw_text.lower()
        if heading_pattern.match(raw_text):
            heading_level = "h1" if h1_keywords.search(text_lower) else "h2"
        elif not raw_text.endswith((".", "!", "?", '"', "”", "’", "»")):
            identifying_attrs = (
                _get_attr(block, "id").lower()
                + " "
                + _get_attr(block, "class").lower()
            )
            if (
                re.search(r"\b(chapter|title|chap|heading)\b", identifying_attrs)
                or "toc" in identifying_attrs
                or (
                    known_toc_titles
                    and text_lower in known_toc_titles
                    and len(raw_text) < 60
                )
            ):
                heading_level = "h1" if "title" in identifying_attrs else "h2"
            if not heading_level:
                for span in block.css("span, font"):
                    style = _get_attr(span, "style").lower()
                    if "bold" not in style and "700" not in style:
                        continue
                    match = re.search(r"font-size:\s*([\d.]+)em", style)
                    if not match:
                        continue
                    size = float(match.group(1))
                    if size >= 1.5:
                        heading_level = "h1"
                        break
                    if size > 1.1:
                        heading_level = "h2"
                        break
        if heading_level:
            _replace_tag(block, heading_level)
            return


# Descend into container to find the smallest valid heading candidate
def _get_leaf_candidate(container: Node) -> Node:
    descendants = [
        node for node in container.css("p, div, section") if node != container
    ]
    if len(descendants) > 1 or len(container.text(strip=True)) > 200:
        for child in container.css(
            "p, div, section, span, header, img, svg"
        ):
            if child == container:
                continue
            nested = _nodes_in_document_order(child, ("p", "div", "section"))
            if (
                child.tag not in ("img", "svg")
                and nested
            ):
                continue
            text = child.text(strip=True) if child.tag not in ("img", "svg") else ""
            has_media = child.tag in ("img", "svg") or child.css_first(
                "img, image, svg"
            )
            valid_text = bool(
                text and len(text) < 150 and any(char.isalnum() for char in text)
            )
            valid_image = bool(has_media and not any(char.isalnum() for char in text))
            if valid_text or valid_image:
                return child
    return container


def _normalize_epub_html(
    tree: HTMLParser,
    known_toc_titles: Optional[Set[str]] = None,
    current_href: Optional[str] = None,
    rich_toc_map: Optional[Dict[str, Any]] = None,
    next_has_header: bool = False,
) -> None:
    """Master pre-processing pipeline for EPUB HTML."""
    import difflib

    known_toc_titles = known_toc_titles or set()
    rich_toc_map = rich_toc_map or {}

    exterminate_bad_tags(tree)
    if nuke_inline_toc(tree):
        return
    promote_image_headers(tree)
    standardize_formatting(tree)
    fix_span_fragmentation(tree)
    standardize_footnotes(tree)

    def finish() -> None:
        strip_junk_attributes(tree)
        heavy_paragraph_cleanup(tree)

    existing_headers = [
        heading
        for heading in _nodes_in_document_order(tree, ("h1", "h2", "h3"))
        if len(heading.text(strip=True)) > 2
    ]
    clean_href = (
        current_href.split("/")[-1].split("#")[0].lower()
        if current_href
        else ""
    )
    expected_nodes = rich_toc_map.get(clean_href, [])

    if existing_headers or next_has_header:
        finish()
        return

    if expected_nodes:
        primary = expected_nodes[0]
        anchor = str(primary.get("anchor", ""))
        title = re.sub(
            r"\s+", " ", str(primary.get("title", ""))
        ).strip().lower()
        target = None

        if anchor:
            target = _find_by_attr(tree, "id", anchor) or _find_by_attr(
                tree, "name", anchor
            )
        if target is None and title and len(title) > 2:
            candidates = _nodes_in_document_order(
                tree,
                (
                    "h1", "h2", "h3", "h4", "h5", "h6",
                    "p", "div", "span", "header",
                ),
            )[:30]
            for block in candidates:
                block_text = re.sub(
                    r"\s+", " ", block.text(separator=" ", strip=True)
                ).lower()
                if len(block_text) < 2:
                    continue
                if (
                    block_text == title
                    or title.startswith(block_text)
                    or block_text.startswith(title)
                    or difflib.SequenceMatcher(None, title, block_text).ratio()
                    >= 0.85
                ):
                    target = block
                    break

        if target is not None:
            target = _get_leaf_candidate(target)
            if target.tag not in (
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "p",
                "div",
                "span",
                "section",
                "header",
            ):
                target = _find_parent(
                    target,
                    (
                        "h1",
                        "h2",
                        "h3",
                        "h4",
                        "h5",
                        "h6",
                        "p",
                        "div",
                        "span",
                        "header",
                    ),
                )
            _replace_tag(target, "h1")
            finish()
            return

        if inject_mapped_image_headings(tree, current_href or "", rich_toc_map):
            finish()
            return

        body = tree.body or tree.root
        if body is None:
            finish()
            return
        for element in _nodes_in_document_order(
            body, ("p", "div", "section", "header", "img", "svg")
        ):
            nested = _nodes_in_document_order(element, ("p", "div", "section"))
            if (
                element.tag not in ("img", "svg")
                and nested
            ):
                continue
            text = (
                element.text(strip=True)
                if element.tag not in ("img", "svg")
                else ""
            )
            has_media = element.tag in ("img", "svg") or element.css_first(
                "img, image, svg"
            )
            if text and len(text) > 150:
                break
            valid_text = bool(
                text and len(text) <= 150 and any(char.isalnum() for char in text)
            )
            valid_image = bool(has_media and not any(char.isalnum() for char in text))
            if not (valid_text or valid_image):
                continue
            if element.tag in ("img", "svg"):
                parent = _find_parent(element, ("p", "div", "section"))
                if parent is not None and len(parent.text(strip=True)) < 150:
                    _replace_tag(parent, "h1")
                else:
                    _replace_with_html(element, f"<h1>{element.html or ''}</h1>")
            else:
                _replace_tag(element, "h1")
            finish()
            return

    if rich_toc_map:
        finish()
        return

    inject_image_headings(tree, known_toc_titles)
    if not tree.css_first("h1, h2, h3"):
        apply_super_fallback_headings(tree)
    finish()

nodes_in_document_order = _nodes_in_document_order
replace_with_html = _replace_with_html



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


# Inject protection before burn
def force_formatting_markers(tree: HTMLParser, current_href: str = "") -> None:
    """
    Force every formatting tag and style into indestructible markers.
    Runs on the live tree as early as possible.
    """
    def normalize_paired_id(raw_id: str) -> str:
        if not raw_id:
            return ""
        s = raw_id.strip()
        m = re.match(r"^([a-zA-Z_\-]*?)(\d+)([a-zA-Z_\-]*)$", s)
        if m:
            pref, num, suf = m.group(1).lower(), m.group(2), m.group(3).lower()
            return f"{pref}_{num}"
        return s.lower()

    def make_det_id(target_file, target_id):
        if not target_file: target_file = current_href
        elif target_file != current_href and current_href:
            base_dir = posixpath.dirname(current_href)
            target_file = posixpath.normpath(posixpath.join(base_dir, target_file))

        norm_id = normalize_paired_id(target_id)
        safe_file = re.sub(r'[^a-zA-Z0-9]', '_', target_file)
        safe_id = re.sub(r'[^a-zA-Z0-9]', '_', norm_id)
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
    # Automated <sup> footnote matching: No keyword reliance.
    # Matches <sup> anchor tags, epub:type="noteref", or role="doc-noteref".
    for anchor in list(tree.css("a")):
        if anchor.parent is None:
            continue

        # Skip anchors inside footnote definition blocks
        p_check = anchor.parent
        in_def = False
        while p_check:
            p_attrs = p_check.attributes or {}
            if p_check.tag in ("aside", "dd") or (p_attrs.get("epub:type") or "").lower() in ("footnote", "endnote") or (p_attrs.get("role") or "").lower() in ("doc-footnote", "doc-endnote"):
                in_def = True
                break
            p_check = p_check.parent
        if in_def:
            continue

        attrs = anchor.attributes or {}
        epub_type = (attrs.get("epub:type") or "").lower()
        role = (attrs.get("role") or "").lower()
        sup_tag = find_parent(anchor, "sup") or anchor.css_first("sup")
        href = (attrs.get("href") or "").strip()

        is_callout = (
            epub_type == "noteref"
            or role == "doc-noteref"
            or (sup_tag is not None and "#" in href)
        )
        if not is_callout or "#" not in href:
            continue

        parts = href.split("#")
        if len(parts) > 1 and parts[1].strip():
            det_id = make_det_id(parts[0], parts[1].strip())
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
        block_id = attrs.get("id", "")
        classes = (attrs.get("class") or "").lower()

        # Check for backlink inside block
        backlink = next(
            (
                anchor for anchor in block.css("a")
                if (anchor.attributes or {}).get("epub:type") == "backlink"
                or (anchor.attributes or {}).get("role") == "doc-backlink"
                or "↑" in anchor.text()
                or "↩" in anchor.text()
                or "^" in anchor.text()
                or (block_id and "#" in ((anchor.attributes or {}).get("href") or ""))
            ),
            None,
        )

        is_def_block = (
            epub_type in ("footnote", "endnote")
            or role in ("doc-footnote", "doc-endnote")
            or "footnote" in classes
            or (block.tag in ("li", "p", "aside", "dd") and backlink is not None and (
                normalize_paired_id(block_id) != block_id or bool(block_id)
            ))
        )
        if not is_def_block:
            continue
        if "@@F_ON@@" in block.text():
            continue

        if not block_id and backlink:
            backlink_attrs = backlink.attributes or {}
            block_id = backlink_attrs.get("id") or backlink_attrs.get("name") or ""
            if not block_id and "#" in (backlink_attrs.get("href") or ""):
                block_id = backlink_attrs.get("href").split("#")[-1]
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

ORNAMENT_KEYWORDS = (
    "circle", "box", "square", "star", "break", "line", "ornament",
    "orn", "sep", "div", "divider", "fleuron", "diamond", "decoration",
)

CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]+)['"]?\s*\)""", re.I)

CSS_ORNAMENT_KEYWORDS = ("background", "url(", "content:", "image", "list-style")



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
    """Standalone single image HTML stays lazy. Mixed pages, multi-image pages, and <s> images become eager."""
    if not pages:
        return pages
    out = []
    for page_html in pages:
        if not page_html or "epub-image" not in page_html:
            out.append(page_html)
            continue
        img_count = len(_EPUB_IMG_TAG.findall(page_html))
        is_standalone = img_count == 1 and not html_has_narrative_besides_media(page_html)
        page_loading = "lazy" if is_standalone else "eager"
        rewritten = _rewrite_epub_image_loading(page_html, page_loading)
        rewritten = _S_BLOCK.sub(
            lambda m: _rewrite_epub_image_loading(m.group(0), "eager"),
            rewritten,
        )
        out.append(rewritten)
    return out



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
        if curr.tag == "section":
            break
        if curr.text(strip=True) or len(curr.css("img, image")) > 1:
            break
        top_node = curr
        curr = curr.parent
    return top_node


def ornament_scene_break_open_tag(top_node, img=None) -> str:
    """Prefer a wrapper/img id so <s> keeps the TTS target after the wrap."""
    candidates = [top_node, img]
    if top_node is not None:
        candidates.append(top_node.css_first("[id], [data-orig-id]"))
    for node in candidates:
        if node is None:
            continue
        attrs = node.attributes or {}
        if attrs.get("id") or attrs.get("data-orig-id"):
            return scene_break_open_tag(node)
    return "<s>"



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
        if media:
            for child in list(iter_children(s_tag, include_text=True)):
                if child.parent is None:
                    continue
                if child == media or child.tag in ("img", "image", "svg"):
                    continue
                if child.css_first("img, image, svg"):
                    continue
                child.decompose()
                modified = True
        # Climb stopped at the img: drop empty div/p wrappers still sitting around <s>.
        curr = s_tag.parent
        while curr is not None and curr.tag in SCENE_BREAK_INNER_WRAPPERS:
            if curr.tag == "section":
                break
            parent = curr.parent
            if parent is None:
                break
            if curr.text(strip=True):
                break
            if len(list(curr.css("s"))) != 1:
                break
            if len(curr.css("img, image, svg")) > len(s_tag.css("img, image, svg")):
                break
            curr.unwrap()
            modified = True
            curr = parent
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
