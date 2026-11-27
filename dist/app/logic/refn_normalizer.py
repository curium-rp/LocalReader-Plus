"""
Reference / footnote normalizer (refn).

Detection uses standard semantics, reference shape and reciprocal hyperlinks.
Publisher classes, identifier prefixes and note vocabulary are never consulted.

The whole book is analyzed once, when the manifest is built:

    footnotes, ops = analyze_book(htmls, spine, preprocess)

- ``footnotes`` holds ids and page indexes only (info.json + frontend).
- ``ops`` is a per-document list of edits found by the analysis. Each chapter
  replays its list with ``apply_ops()`` instead of re-running detection.

An op addresses a node by its position in ``tree.css('*')`` of the freshly
parsed chapter (``"12:a"``) or by a span the op list itself inserted (``"@0"``):

    m <loc> <kind>                 mark(node, kind)
    a <loc> <key> <value>          node.attrs[key] = value
    s <loc> <kind> <id>            insert an id span (before a noteref, inside a footnote)
"""
from __future__ import annotations

import hashlib
import html
import posixpath
import re
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

REFN_VERSION = 4
_BLOCKS = {'p', 'li', 'dd', 'aside', 'div', 'section', 'blockquote'}
_NOTE_TYPES = {'footnote', 'endnote'}
_NOTE_ROLES = {'doc-footnote', 'doc-endnote'}
_COLLECTION_TYPES = {'footnotes', 'endnotes'}
_SEMANTIC_KEYS = {'epub:type', 'role'}


# ---------------------------------------------------------------------------
# Node helpers. Identity is mem_id: LexborNode.__eq__ compares serialized HTML.
# ---------------------------------------------------------------------------
def attr(node, key):
    return str((node.attributes or {}).get(key) or '') if node is not None else ''


def tokens(node, key):
    return set(attr(node, key).lower().split())


def same(a, b):
    return a is not None and b is not None and a.mem_id == b.mem_id


def document_name(name):
    return posixpath.normpath(str(name).replace('\\', '/')).lstrip('/')


def resolve_href(document, href):
    try:
        parsed = urlsplit(str(href or '').strip())
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.fragment:
        return None
    path = unquote(parsed.path)
    target = posixpath.join(posixpath.dirname(document), path) if path else document
    return document_name(target), unquote(parsed.fragment)


def is_definition(node):
    return bool(tokens(node, 'epub:type') & _NOTE_TYPES or tokens(node, 'role') & _NOTE_ROLES)


def is_collection(node):
    return bool(tokens(node, 'epub:type') & _COLLECTION_TYPES or 'doc-endnotes' in tokens(node, 'role'))


def contains(parent, node):
    if parent is None:
        return False
    target = parent.mem_id
    while node is not None:
        if node.mem_id == target:
            return True
        node = node.parent
    return False


def inside_definition(node):
    while node is not None:
        if is_definition(node):
            return True
        node = node.parent
    return False


def reference_hint(node):
    if 'noteref' in tokens(node, 'epub:type') or 'doc-noteref' in tokens(node, 'role'):
        return True
    current = node
    while current is not None and current.tag not in _BLOCKS:
        if current.tag == 'sup':
            return True
        current = current.parent
    return node.css_first('sup') is not None


def compact_marker(node):
    raw = node.text(strip=True)
    label = raw.strip('[](). \t\n')
    if not label or len(label) > 8:
        return False
    if len(label) == 1 and label.isalpha() and raw != label:
        return True
    return all(char.isnumeric() or unicodedata.category(char)[0] in {'P', 'S'} for char in label)


def definition_block(node):
    """Use the target itself, then its nearest text owner; never a chapter ancestor."""
    current = node
    while current is not None and current.tag not in {'body', 'html', '#document'}:
        if current.tag in _BLOCKS:
            if is_collection(current):
                return None
            if current.tag in {'p', 'li', 'dd'} or is_definition(current):
                return current
            # A target on an inline marker must not promote a whole chapter div.
            if any(child.mem_id != current.mem_id for child in current.css('p, li, dd, aside, section, div')):
                return None
            return current
        current = current.parent
    return None


def mark(node, kind):
    """Stamp standard note semantics; the frontend reads only these attributes."""
    if node is None:
        return
    classes = [value for value in attr(node, 'class').split() if value not in {'epub-noteref', 'epub-backlink'}]
    if 'epub-' + kind not in classes:
        classes.append('epub-' + kind)
    _set(node, 'class', ' '.join(classes))
    if kind in {'noteref', 'backlink'} and node.tag != 'a':
        _set(node, 'data-footnote-anchor', kind)
        return
    types = [value for value in attr(node, 'epub:type').split()
             if value.lower() not in {'noteref', 'backlink', 'footnote', 'endnote'}]
    semantic_kind = 'endnote' if kind == 'footnote' and (
        'endnote' in tokens(node, 'epub:type') or 'doc-endnote' in tokens(node, 'role')) else kind
    if semantic_kind not in types:
        types.append(semantic_kind)
    _set(node, 'epub:type', ' '.join(types))
    role = {'noteref': 'doc-noteref', 'backlink': 'doc-backlink'}.get(kind)
    if role:
        roles = [value for value in attr(node, 'role').split() if value.lower() not in {'doc-noteref', 'doc-backlink'}]
        _set(node, 'role', ' '.join([*roles, role]))


def _set(node, key, value):
    if (node.attributes or {}).get(key) != value:
        node.attrs[key] = value


def _ids(node):
    return {attr(node, key) for key in ('id', 'name', 'data-orig-id')} - {''}


def _is_text(node):
    return node.tag.startswith(('-', '#', '_', '!'))


def _is_carrier(node):
    """An empty, non-link element whose only job is to hold an id (``<a id=x/>``)."""
    return (node is not None and not _is_text(node) and bool(_ids(node)) and not attr(node, 'href')
            and not node.text(strip=True) and node.css_first('img, svg, image, video, object') is None)


def _neighbors(node):
    """The nearest non-blank sibling on each side."""
    for step in ('prev', 'next'):
        sibling = getattr(node, step)
        while sibling is not None and _is_text(sibling) and not sibling.text(deep=True).strip():
            sibling = getattr(sibling, step)
        if sibling is not None:
            yield sibling


def _source_ids(node):
    """Ids naming a link's position: its own, inline ancestors', and adjacent empty carriers'."""
    values = set()
    current = node
    while current is not None and current.tag not in {'body', 'html', '#document'}:
        values.update(_ids(current))
        if current.tag in _BLOCKS:
            break
        for sibling in _neighbors(current):
            if _is_carrier(sibling):
                values.update(_ids(sibling))
        current = current.parent
    return values


def _link_at(node):
    """The link standing at a target: the node itself, or the link beside an empty id carrier."""
    if node is None:
        return None
    if node.tag == 'a' and attr(node, 'href'):
        return node
    if not _is_carrier(node):
        return None
    for sibling in _neighbors(node):
        if _is_text(sibling):
            continue
        if sibling.tag == 'a' and attr(sibling, 'href'):
            return sibling
        if sibling.tag not in _BLOCKS:
            links = sibling.css('a[href]')
            if len(links) == 1:
                return links[0]
    return None


def block_position(node):
    """'lead' when no word precedes the node in its text block, 'prose' when words do, else None."""
    current, position = node, 'lead'
    while current is not None and current.tag not in _BLOCKS | {'body', 'html', '#document'}:
        sibling = current.prev
        while sibling is not None and position == 'lead':
            if any(char.isalnum() for char in sibling.text(deep=True)):
                position = 'prose'
            sibling = sibling.prev
        current = current.parent
    return position if current is not None and current.tag in _BLOCKS else None


def _signature(fragment):
    match = re.fullmatch(r'(.*?)(\d+)([^\d]*)', fragment)
    return match.groups() if match else None


def _id_span(wanted, kind):
    markup = f'<span id="{html.escape(wanted, quote=True)}" data-footnote-anchor="{kind}"></span>'
    return HTMLParser(markup).css_first('span')


def _insert_id_span(node, wanted, kind):
    """Give a node that already owns an id a second, empty id carrier."""
    if kind == 'noteref':
        node.insert_before(_id_span(wanted, kind))
        return node.prev
    node.insert_child(_id_span(wanted, kind))
    return next(child for child in node.css('[id]') if attr(child, 'id') == wanted)


# ---------------------------------------------------------------------------
# Whole-book analysis: edits the throwaway trees and records each edit as an op.
# ---------------------------------------------------------------------------
class _Doc:
    def __init__(self, name, tree):
        self.name = name
        self.tree = tree
        self.order = {node.mem_id: index for index, node in enumerate(tree.css('*'))}
        self.spans: Dict[int, int] = {}
        self.ops: List[str] = []

    def loc(self, node):
        if node.mem_id in self.spans:
            return f'@{self.spans[node.mem_id]}'
        return f'{self.order[node.mem_id]}:{node.tag}'

    def record(self, op):
        if not self.ops or self.ops[-1] != op:
            self.ops.append(op)


class _Book:
    def __init__(self, docs: Dict[str, _Doc]):
        self.docs = docs
        self.owner: Dict[int, _Doc] = {}
        for doc in docs.values():
            for mem_id in doc.order:
                self.owner[mem_id] = doc
        self._blocks: Dict[int, Optional[Node]] = {}

    def doc_of(self, node) -> _Doc:
        return self.owner[node.mem_id]

    def block(self, node):
        if node is None:
            return None
        key = node.mem_id
        if key not in self._blocks:
            self._blocks[key] = definition_block(node)
        return self._blocks[key]

    def set(self, node, key, value):
        if (node.attributes or {}).get(key) == value:
            return
        node.attrs[key] = value
        self.doc_of(node).record(f'a {self.doc_of(node).loc(node)} {key} {value}')
        if key in _SEMANTIC_KEYS:
            self._blocks.clear()

    def mark(self, node, kind):
        if node is None:
            return
        mark(node, kind)
        self.doc_of(node).record(f'm {self.doc_of(node).loc(node)} {kind}')
        self._blocks.clear()

    def ensure_id(self, node, wanted, index, kind):
        doc = self.doc_of(node)
        key = (doc.name, wanted)
        if key in index:
            return wanted
        if not attr(node, 'id'):
            self.set(node, 'id', wanted)
            index[key] = node
            return wanted
        doc.record(f's {doc.loc(node)} {kind} {wanted}')
        span = _insert_id_span(node, wanted, kind)
        doc.spans[span.mem_id] = len(doc.spans)
        self.owner[span.mem_id] = doc
        index[key] = span
        return wanted


def _analyze(book: _Book):
    """Return note records [{document, anchor_id, definition_ids, callouts}]."""
    index, links, incoming, by_file = {}, [], {}, {}
    for document, doc in book.docs.items():
        for node in doc.tree.css('.epub-footnote'):
            if is_collection(node):
                book.set(node, 'class', ' '.join(value for value in attr(node, 'class').split() if value != 'epub-footnote'))
        for node in doc.tree.css('[id], [name], [data-orig-id]'):
            for identifier in _ids(node):
                index.setdefault((document, identifier), node)
        for ordinal, node in enumerate(doc.tree.css('a[href]')):
            target = resolve_href(document, attr(node, 'href'))
            if target:
                link = {'file': document, 'node': node, 'target': target, 'ordinal': ordinal,
                        'hint': reference_hint(node), 'marker': compact_marker(node),
                        'source_ids': _source_ids(node), 'signature': _signature(target[1])}
                links.append(link)
                incoming.setdefault(target, []).append(link)
                by_file.setdefault(document, []).append(link)

    records, record_nodes, owned = {}, {}, {}

    def register(document, block, fragment):
        owner = (document, block.mem_id)
        fragment = book.ensure_id(block, fragment, index, 'footnote')
        book.set(index[(document, fragment)], 'data-footnote-anchor', 'footnote')
        if owner in owned:
            if fragment not in owned[owner]['definition_ids']:
                owned[owner]['definition_ids'].append(fragment)
            return owned[owner]
        book.mark(block, 'footnote')
        rec = {'document': document, 'anchor_id': fragment, 'definition_ids': [fragment], 'callouts': []}
        records[(document, fragment)] = rec
        record_nodes[(document, fragment)] = block
        owned[owner] = rec
        return rec

    # Standard semantic definitions also survive when no reference remains.
    for document, doc in book.docs.items():
        for node in doc.tree.css('[epub\\:type], [role]'):
            if is_definition(node) and not is_collection(node) and _ids(node):
                block = book.block(node)
                if block is not None:
                    register(document, block, attr(node, 'id') or attr(node, 'name') or attr(node, 'data-orig-id'))

    # Prefer explicit/superscript forward edges before considering untyped cycles.
    for link in sorted(links, key=lambda item: not item['hint']):
        node, document = link['node'], link['file']
        if inside_definition(node) or 'backlink' in tokens(node, 'epub:type') or 'doc-backlink' in tokens(node, 'role'):
            continue
        target = link['target']
        direct = index.get(target)
        peer = _link_at(direct)
        if peer is not None and resolve_href(target[0], attr(peer, 'href')):
            # A return edge targets the callout anchor, not the definition owner.
            # In a marker cycle the definition's marker opens its block; the callout sits in prose.
            callout_like = reference_hint(peer) or (
                not link['hint'] and compact_marker(peer) and not inside_definition(peer))
            if callout_like and not (block_position(peer) == 'lead' and block_position(node) == 'prose'):
                continue
        block = book.block(direct)
        own_block = book.block(node)
        reciprocal = [back for source_id in link['source_ids']
                      for back in incoming.get((document, source_id), [])
                      if not same(back['node'], node) and not contains(own_block, back['node'])]
        semantic = direct is not None and inside_definition(direct)
        descriptive_cycle = False
        if block is not None:
            for back in reciprocal:
                if contains(block, back['node']) and back['marker']:
                    remainder = block.text(strip=True).replace(back['node'].text(strip=True), '', 1)
                    descriptive_cycle = any(char.isalnum() for char in remainder)
                    if descriptive_cycle:
                        break
        if not (link['hint'] or semantic or (link['marker'] and reciprocal) or descriptive_cycle):
            # An unproven compact link is retained as an ordinary EPUB jump.
            # Protect its endpoints from cleanup without asserting note semantics.
            if link['marker']:
                book.set(node, 'data-footnote-anchor', 'candidate')
                if block is not None and not contains(block, node):
                    book.set(block, 'data-footnote-anchor', 'candidate')
            continue
        if block is None and reciprocal:
            owners = {}
            for back in reciprocal:
                back_block = book.block(back['node'])
                if back_block is not None:
                    owners[(back['file'], back_block.mem_id)] = back
            if len(owners) == 1:
                back = next(iter(owners.values()))
                if back['file'] == target[0]:
                    block = book.block(back['node'])
        if block is None and link['hint'] and link['signature']:
            signature = link['signature']
            matches = [back for back in by_file.get(target[0], [])
                       if back['target'][0] == document and not same(back['node'], node)
                       and not back['hint'] and back['signature']
                       and signature[:2] == back['signature'][:2] and signature[2] != back['signature'][2]
                       and book.block(back['node']) is not None]
            if len(matches) == 1:
                reciprocal = matches
                block = book.block(matches[0]['node'])
                source_id = matches[0]['target'][1]
                book.ensure_id(node, source_id, index, 'noteref')
                link['source_ids'].add(source_id)
        if block is None or contains(block, node):
            # Keep strongly hinted unresolved links usable; do not invent a definition.
            if link['hint']:
                book.mark(node, 'noteref')
            continue
        rec = register(target[0], block, target[1])
        # A unique orphan return edge supplies an exporter-stripped source id.
        returns = [back for back in by_file.get(target[0], [])
                   if back['target'][0] == document and contains(block, back['node'])
                   and (back['marker'] or 'backlink' in tokens(back['node'], 'epub:type'))]
        orphan_ids = {back['target'][1] for back in returns if back['target'] not in index}
        source_id = attr(node, 'id') or attr(node, 'name')
        if not source_id:
            existing_returns = {back['target'][1] for back in returns
                                if back['target'][1] in link['source_ids']}
            if len(existing_returns) == 1:
                source_id = next(iter(existing_returns))
            else:
                parent = node.parent
                while parent is not None and parent.tag not in _BLOCKS | {'body', 'html'}:
                    source_id = attr(parent, 'id') or attr(parent, 'name')
                    if source_id:
                        break
                    parent = parent.parent
        if not source_id:
            source_id = next(iter(orphan_ids)) if len(orphan_ids) == 1 else (
                'fnref-auto-' + hashlib.sha256(f'{document}|{target}|{link["ordinal"]}'.encode()).hexdigest()[:16])
            book.ensure_id(node, source_id, index, 'noteref')
        link['source_ids'].add(source_id)
        book.mark(node, 'noteref')
        callout = {'document': document, 'anchor_id': source_id}
        if callout not in rec['callouts']:
            rec['callouts'].append(callout)
        for back in returns:
            if back['target'][1] in link['source_ids']:
                book.mark(back['node'], 'backlink')
                target_node = index.get(back['target'])
                if target_node is not None and not same(target_node, node):
                    book.set(target_node, 'data-footnote-anchor', 'noteref')

    # A referenced item establishes a note list structurally, including orphan siblings.
    for (document, _), rec in list(records.items()):
        block = record_nodes[(document, rec['anchor_id'])]
        parent = block.parent
        if block.tag == 'li' and parent is not None and parent.tag in {'ol', 'ul'}:
            if not is_collection(parent):
                book.set(parent, 'epub:type', 'footnotes')
            for sibling in parent.css('li'):
                if same(sibling.parent, parent) and attr(sibling, 'id'):
                    register(document, sibling, attr(sibling, 'id'))
            ancestor = parent.parent
            if ancestor is not None and ancestor.tag == 'aside' and not is_definition(ancestor):
                book.set(ancestor, 'epub:type', 'footnotes')
    return list(records.values())


def analyze_book(
    htmls: Dict[str, Any],
    spine: Optional[List[str]],
    preprocess: Callable[[str], str] = lambda value: value,
) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Detect notes across the reader spine. Returns (footnotes, ops by document)."""
    spine = [document_name(name) for name in (spine or htmls)]
    raw = {document_name(name): value for name, value in htmls.items()}
    for name in spine:
        if name not in raw:
            matches = [key for key in raw if posixpath.basename(key) == posixpath.basename(name)]
            if len(matches) == 1:
                raw[name] = raw[matches[0]]
    docs = {}
    for name in spine:
        if name in raw and name not in docs:
            markup = raw[name].decode('utf-8', errors='ignore') if isinstance(raw[name], bytes) else raw[name]
            docs[name] = _Doc(name, HTMLParser(preprocess(markup)))
    records = _analyze(_Book(docs))
    pages = {name: idx for idx, name in enumerate(spine)}
    notes = []
    for rec in records:
        note = {'page': pages.get(rec['document'], -1), 'ids': rec['definition_ids'],
                'refs': [[pages.get(item['document'], -1), item['anchor_id']] for item in rec['callouts']]}
        notes.append(note)
    ops = {name: doc.ops for name, doc in docs.items() if doc.ops}
    return {'v': REFN_VERSION, 'notes': notes}, ops


# ---------------------------------------------------------------------------
# Chapter replay
# ---------------------------------------------------------------------------
def apply_ops(tree: HTMLParser, ops: Optional[List[str]]) -> bool:
    """Replay one document's ops on a fresh tree. A stale list is skipped whole."""
    if not ops:
        return False
    nodes = tree.css('*')
    for op in ops:
        loc = op.split(' ', 2)[1]
        if loc.startswith('@'):
            continue
        position, _, tag = loc.partition(':')
        index = int(position)
        if index >= len(nodes) or nodes[index].tag != tag:
            return False
    spans: List[Node] = []

    def resolve(loc):
        if loc.startswith('@'):
            slot = int(loc[1:])
            return spans[slot] if slot < len(spans) else None
        return nodes[int(loc.partition(':')[0])]

    for op in ops:
        code, loc, rest = op.split(' ', 2)
        node = resolve(loc)
        if node is None:
            continue
        if code == 'm':
            mark(node, rest)
        elif code == 'a':
            key, _, value = rest.partition(' ')
            _set(node, key, value)
        elif code == 's':
            kind, _, wanted = rest.partition(' ')
            spans.append(_insert_id_span(node, wanted, kind))
    return True


# ---------------------------------------------------------------------------
# Lookup: aliases are rebuilt in RAM from notes + spine, never stored.
# ---------------------------------------------------------------------------
def note_index(footnotes: Optional[Dict[str, Any]], spine: Optional[List[str]]) -> Dict[str, int]:
    """Map 'doc#id', unique 'basename#id' and unique bare 'id' to a note position."""
    spine = [document_name(name) for name in (spine or [])]
    notes = (footnotes or {}).get('notes') or []
    base_count: Dict[str, int] = {}
    for name in spine:
        base = posixpath.basename(name)
        base_count[base] = base_count.get(base, 0) + 1
    index: Dict[str, int] = {}
    bare: Dict[str, set] = {}
    for position, note in enumerate(notes):
        aliases = [(note.get('page', -1), identifier) for identifier in note.get('ids') or []]
        aliases += [(page, identifier) for page, identifier in note.get('refs') or []]
        for page, fragment in aliases:
            document = spine[page] if 0 <= page < len(spine) else ''
            if document:
                index[f'{document}#{fragment}'] = position
                base = posixpath.basename(document)
                if base_count.get(base) == 1:
                    index[f'{base}#{fragment}'] = position
            bare.setdefault(fragment, set()).add(position)
    for fragment, positions in bare.items():
        if len(positions) == 1 and fragment not in index:
            index[fragment] = next(iter(positions))
    return index


def lookup_note(footnotes, spine, index, file='', fragment='') -> Optional[Dict[str, Any]]:
    notes = (footnotes or {}).get('notes') or []
    spine = [document_name(name) for name in (spine or [])]
    fragment = unquote(str(fragment or ''))
    file = document_name(unquote(str(file or '').split('?')[0])) if file else ''
    keys = [f'{file}#{fragment}', f'{posixpath.basename(file)}#{fragment}'] if file else []
    for key in [*keys, fragment]:
        position = index.get(key) if key else None
        if position is None or position >= len(notes):
            continue
        note = notes[position]
        if file and key == fragment:
            # A bare id only answers for a file that holds one of the note's endpoints.
            pages = [note.get('page', -1), *(page for page, _ in note.get('refs') or [])]
            documents = {spine[page] for page in pages if 0 <= page < len(spine)}
            if file not in documents and not any(posixpath.basename(path) == file for path in documents):
                continue
        return note
    return None


def remap_note_pages(footnotes: Optional[Dict[str, Any]], remap: Callable[[int], int]) -> None:
    for note in (footnotes or {}).get('notes') or []:
        if note.get('page', -1) >= 0:
            note['page'] = remap(note['page'])
        note['refs'] = [[remap(page) if page >= 0 else page, identifier]
                        for page, identifier in note.get('refs') or []]
