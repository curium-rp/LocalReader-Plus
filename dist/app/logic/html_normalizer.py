import html
import re
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
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
PROTECTED_CLASSES = {
    "epub-footnote",
    "epub-noteref",
    "epub-backlink",
    "epub-image",
    "epub-visually-hidden",
    "pagebreak",
    "page-break",
}


def iter_children(node: Optional[Node]) -> Iterable[Node]:
    """Yield immediate children. Node.iter() yields all descendants."""
    child = node.child if node is not None else None
    while child is not None:
        next_child = child.next
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
            for child in list(iter_children(target)):
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
