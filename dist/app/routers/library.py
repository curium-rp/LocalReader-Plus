from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import json
import os
import time
import re
import uuid
import posixpath
import urllib.parse
import shutil
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from ..config import library_file, content_dir, settings_file
from ..models import LibraryItem, ContentItem
from ..utils import safe_save_json
import sys
from pathlib import Path

base_dir = Path(__file__).parent.parent
if str(base_dir) not in sys.path:
    sys.path.append(str(base_dir))

try:
    from logic.smart_content_detector import (
        detect_headers_footers,
        apply_header_footer_filter,
        detect_strict_scene_break
    )
    from logic.html_normalizer import generate_toc, pre_parse_clean, normalize_epub_html, standardize_footnotes
except ImportError:
    sys.path.append(str(base_dir))
    try:
        from logic.smart_content_detector import (
            detect_headers_footers,
            apply_header_footer_filter,
            detect_strict_scene_break
        )
        from logic.html_normalizer import generate_toc, pre_parse_clean, normalize_epub_html, standardize_footnotes
    except ImportError:
        pass

import asyncio
router = APIRouter()

# 🌟 CONCURRENCY SHIELD: Global lock for all library.json mutations
_library_lock = asyncio.Lock()

class ProgressUpdatePayload(BaseModel):
    currentPage: int
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: int
    lastAccessed: float

def get_doc_json_path(doc_id: str) -> Path:
    new_path = content_dir / doc_id / f"{doc_id}.json"
    if new_path.exists():
        return new_path
    old_path = content_dir / f"{doc_id}.json"
    if old_path.exists():
        return old_path
    raise HTTPException(status_code=404, detail="Document not found")

def force_formatting_markers(soup: BeautifulSoup, current_href: str = "") -> None:
    """
    Force every formatting tag and style into indestructible markers.
    Runs on the live tree as early as possible.
    """
    import re
    import posixpath
    
    def make_det_id(target_file, target_id):
        if not target_file: target_file = current_href
        elif target_file != current_href and current_href:
            base_dir = posixpath.dirname(current_href)
            target_file = posixpath.normpath(posixpath.join(base_dir, target_file))
        
        safe_file = re.sub(r'[^a-zA-Z0-9]', '_', target_file)
        safe_id = re.sub(r'[^a-zA-Z0-9]', '_', target_id)
        return f"R_{safe_file}_{safe_id}"

    # 🌟 FOOTNOTE EXTRACTION SHIELD
    for a in list(soup.find_all('a', attrs={'epub:type': 'noteref'})):
        href = a.get('href', '')
        if '#' not in href: continue
        
        parts = href.split('#')
        if len(parts) > 1:
            det_id = make_det_id(parts[0], parts[1])
            
            sup_tag = a.find_parent('sup') or a.find('sup')
            tag_type = "SUP" if sup_tag else "A"
            
            a.insert(0, soup.new_string(f'§§F_ON§§ §§F_S|{det_id}|{tag_type}§§ '))
            a.append(soup.new_string(f' §§F_OFF_{tag_type}§§'))
            
            if sup_tag: sup_tag.unwrap()
            a.unwrap()
        
    for block in list(soup.find_all(attrs={'epub:type': 'footnote'})):
        b_id = block.get('id', '')
        if '§§F_ON§§' in block.get_text(): continue
        
        backlink = block.find('a', attrs={'epub:type': 'backlink'})
        if not b_id and backlink:
            b_id = backlink.get('id') or backlink.get('name') or ''
            
        if not b_id: continue
        
        det_id = make_det_id(current_href, b_id)
        if backlink:
            backlink.insert(0, soup.new_string(f'§§F_ON§§ §§F_E|{det_id}§§ '))
            backlink.append(soup.new_string(f' §§F_OFF_A§§'))
            backlink.unwrap()
        else:
            inner = block.find(['p', 'span', 'div']) or block
            inner.insert(0, soup.new_string(f'§§F_ON§§ §§F_E|{det_id}§§ §§F_OFF_A§§ '))

    # 🌟 FORMATTING SHIELD (RESTORED)
    bold_regex = re.compile(r'\b(bold|bld|strong|calibre_bold|fw-bold|font-bold|b-text)\b', re.IGNORECASE)
    ital_regex = re.compile(r'\b(italic|it|em|emphasis|oblique|calibre_italic|fs-italic|i-text)\b', re.IGNORECASE)
    und_regex = re.compile(r'\b(underline|u-text|calibre_under)\b', re.IGNORECASE)
    del_regex = re.compile(r'\b(strike|strikethrough|line-through|del)\b', re.IGNORECASE)

    for tag in list(soup.find_all(['span', 'font', 'p', 'div', 'a'])):
        style = tag.get('style', '').lower()
        class_str = " ".join(tag.get('class', [])).lower()
        
        is_bold = 'bold' in style or '600' in style or '700' in style or '800' in style or '900' in style or 'bolder' in style or bold_regex.search(class_str)
        is_ital = 'italic' in style or 'oblique' in style or ital_regex.search(class_str)
        is_und = 'underline' in style or und_regex.search(class_str)
        is_del = 'line-through' in style or del_regex.search(class_str)
        
        if is_bold or is_ital or is_und or is_del:
            if is_bold:
                tag.insert(0, soup.new_string('§§B_ON§§'))
                tag.append(soup.new_string('§§B_OFF§§'))
            if is_ital:
                tag.insert(0, soup.new_string('§§I_ON§§'))
                tag.append(soup.new_string('§§I_OFF§§'))
            if is_und:
                tag.insert(0, soup.new_string('§§U_ON§§'))
                tag.append(soup.new_string('§§U_OFF§§'))
            if is_del:
                tag.insert(0, soup.new_string('§§D_ON§§'))
                tag.append(soup.new_string('§§D_OFF§§'))
            
            if 'style' in tag.attrs: del tag['style']
            if 'class' in tag.attrs: del tag['class']
            
            if tag.name in ['span', 'font']:
                tag.unwrap()

    mapping = [
        (['b', 'strong'], '§§B_ON§§', '§§B_OFF§§'),
        (['i', 'em', 'cite', 'dfn'], '§§I_ON§§', '§§I_OFF§§'),
        (['u', 'ins'], '§§U_ON§§', '§§U_OFF§§'),
        (['del', 's', 'strike'], '§§D_ON§§', '§§D_OFF§§'),
    ]

    for tags, on, off in mapping:
        for tag in list(soup.find_all(tags)):
            tag.insert_before(soup.new_string(on))
            tag.insert_after(soup.new_string(off))
            tag.unwrap()

    for br in list(soup.find_all('br')):
        br.replace_with(soup.new_string('§§BR§§'))

def master_sentence_splitter(text: str, start_idx: int = 0):
    text = text.strip()
    if not text:
        return "", start_idx

    import re
    text = re.sub(r'\.\s+\.\s+\.', '...', text)

    abbreviations = [
    # Titles and honorifics
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Rev", "Hon", "Jr", "Sr", "Esq",
    "Messrs", "Mmes", "Fr", "Pres",
    # Military ranks
    "Gen", "Col", "Maj", "Capt", "Lt", "Sgt", "Cpl", "Pvt", "Adm", "Cmdr", "Brig",
    # Political and legal titles
    "Sen", "Rep", "Gov", "Amb", "Atty", "Cllr",
    # Addresses and locations
    "St", "Rd", "Ave", "Blvd", "Ln", "Dr", "Ct", "Pl", "Sq", "Ter", "Pkwy", "Hwy",
    "Apt", "Ste", "Bldg",
    # Business and corporate
    "Co", "Inc", "Ltd", "Corp", "LLC", "Mfg",
    # Latin, academic, and references
    "vs", "viz", "etc", "eg", "ie", "al", "ca", "cf", "ibid", "op",
    "Fig", "Figs", "No", "Nos", "Vol", "Vols", "ch", "sec", "ed", "eds",
    "pp", "p", "approx", "dept", "est",
    # Months
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"
    ]
    for abbr in abbreviations:
        text = re.sub(rf'\b({abbr})\.(?=\s)', r'\1<ABBR>', text, flags=re.IGNORECASE)

    text = re.sub(r'(?i)\b(e\.g)\.(?=\s)', r'\1<ABBR>', text)
    text = re.sub(r'(?i)\b(i\.e)\.(?=\s)', r'\1<ABBR>', text)

    # Simplified regex ignores all §§...§§ markers natively
    pattern = (
        r'(?<=[.])\s+(?=(?:§§[^§]+§§\s*)*[A-Z"\'\u201c\u2018])|'
        r'(?<=[.][\'"”’])\s+(?=(?:§§[^§]+§§\s*)*[A-Z"\'\u201c\u2018])|'
        r'(?<=[。])\s*(?=(?:§§[^§]+§§\s*)*[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])|'
        r'(?<=[。][\'"”’])\s*(?=(?:§§[^§]+§§\s*)*[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])'
    )

    raw_chunks = re.split(pattern, text)
    chunks = [c.strip() for c in raw_chunks if c.strip()]

    html_out = ""
    current_idx = start_idx
    buffer = ""

    is_bold = is_ital = is_und = is_del = False

    for i, c in enumerate(chunks):
        if buffer:
            buffer += " " + c
        else:
            buffer = c

        # Single pass cleanup for word counting
        clean_buf = re.sub(r'§§[^§]+§§', '', buffer)
        word_count = len(re.findall(r'\b\w+\b', clean_buf))

        if word_count < 4 and i != len(chunks) - 1:
            continue

        clean_chunk = buffer.replace('<ABBR>', '.')

        # Restore tags directly from source markers. Includes BR fix.
        clean_chunk = (
            clean_chunk
            .replace('§§B_ON§§', '<b>').replace('§§B_OFF§§', '</b>')
            .replace('§§I_ON§§', '<i>').replace('§§I_OFF§§', '</i>')
            .replace('§§U_ON§§', '<u>').replace('§§U_OFF§§', '</u>')
            .replace('§§D_ON§§', '<del>').replace('§§D_OFF§§', '</del>')
            .replace('§§F_ON§§ ', '').replace('§§F_ON§§', '')
            .replace(' §§F_OFF_A§§', '</a>').replace('§§F_OFF_A§§', '</a>')
            .replace(' §§F_OFF_SUP§§', '</sup></a>').replace('§§F_OFF_SUP§§', '</sup></a>')
            .replace(' §§BR§§ ', '<br/>').replace('§§BR§§', '<br/>')
        )
        
        # Restore footnote structural tags directly
        clean_chunk = re.sub(r'§§F_S\|([^§|]*)\|SUP§§\s*', r'<a epub:type="noteref" href="#\1"><sup>', clean_chunk)
        clean_chunk = re.sub(r'§§F_S\|([^§|]*)\|A§§\s*', r'<a epub:type="noteref" href="#\1">', clean_chunk)
        clean_chunk = re.sub(r'§§F_E\|([^§|]*)§§\s*', r'<a epub:type="footnote" id="\1">', clean_chunk)

        prefix = ""
        if is_ital: prefix += "<i>"
        if is_bold: prefix += "<b>"
        if is_und:  prefix += "<u>"
        if is_del:  prefix += "<del>"
        clean_chunk = prefix + clean_chunk

        for match in re.finditer(r'<(/?)(b|i|u|del)>', clean_chunk):
            is_closing = match.group(1) == '/'
            tag = match.group(2)
            if tag == 'b': is_bold = not is_closing
            if tag == 'i': is_ital = not is_closing
            if tag == 'u': is_und = not is_closing
            if tag == 'del': is_del = not is_closing

        suffix = ""
        if is_del:  suffix += "</del>"
        if is_und:  suffix += "</u>"
        if is_bold: suffix += "</b>"
        if is_ital: suffix += "</i>"
        clean_chunk = clean_chunk + suffix

        clean_chunk = (
            clean_chunk
            .replace('<b></b>', '')
            .replace('<i></i>', '')
            .replace('<u></u>', '')
            .replace('<del></del>', '')
        ).strip()

        visible_text = re.sub(r'<[^>]+>', '', clean_chunk)
        visible_text = re.sub(r'[\s\u200b\u200c\u200d\ufeff]+', '', visible_text)
        
        if visible_text:
            html_out += f'<n id="s_{current_idx}">{clean_chunk}</n> '
            current_idx += 1
            
        buffer = ""

    return html_out.strip(), current_idx


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


def process_css_scene_breaks(pages, master_css):
    if not pages: return pages
        
    from collections import defaultdict
    import re
    
    class_counts = defaultdict(int)
    
    for page_html in pages:
        soup = BeautifulSoup(page_html, 'html.parser')
        for tag in soup.find_all(['hr', 'div', 'p', 'span']):
            if not tag.get_text(strip=True) and not tag.find(['img', 'image', 'svg']):
                classes_str = tag.get('data-orig-class', '')
                classes = classes_str.split() if classes_str else []
                for c in classes:
                    class_counts[c] += 1
                    
    confirmed_classes = set()
    if master_css:
        for c, count in class_counts.items():
            if count >= 4:
                pattern = r'\.' + re.escape(c) + r'\s*\{([^}]+)\}'
                matches = re.findall(pattern, master_css)
                for block in matches:
                    block_lower = block.lower()
                    if any(kw in block_lower for kw in ['background', 'url(', 'content:', 'image', 'list-style']):
                        confirmed_classes.add(c)
                        break
                
    new_pages = []
    for page_html in pages:
        if '<hr' not in page_html and 'data-orig-class=' not in page_html:
            new_pages.append(page_html)
            continue
            
        soup = BeautifulSoup(page_html, 'html.parser')
        modified = False
        
        for tag in soup.find_all(['hr', 'div', 'p', 'span']):
            if not tag.get_text(strip=True) and not tag.find(['img', 'image', 'svg']):
                classes_str = tag.get('data-orig-class', '')
                classes = classes_str.split() if classes_str else []
                
                is_confirmed_css = any(c in confirmed_classes for c in classes)
                
                if is_confirmed_css:
                    # 🌟 STRICT ORNAMENT SHIELD: Enforce sandwich rule
                    prev_text_node = None
                    for curr in tag.find_all_previous(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span']):
                        if curr.get_text(strip=True):
                            prev_text_node = curr
                            break
                            
                    if not prev_text_node or prev_text_node.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] or prev_text_node.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        continue

                    s_tag = soup.new_tag('s')
                    s_tag.string = "◆ ◆ ◆"
                    
                    orig_id = tag.get('id')
                    data_id = tag.get('data-orig-id')
                    if orig_id: s_tag['id'] = orig_id
                    if data_id: s_tag['data-orig-id'] = data_id
                    
                    tag.replace_with(s_tag)
                    modified = True
        
        if modified:
            body = soup.find('body')
            page_str = str(body) if body else str(soup)
            page_str = re.sub(r'>\s*\n+\s*<', '><', page_str)
        else:
            page_str = page_html
            
        # 🔥 INCINERATOR: Wipe the temporary class tracking attribute from ALL tags globally
        page_str = re.sub(r'\s*data-orig-class="[^"]*"', '', page_str)
        page_str = re.sub(r"\s*data-orig-class='[^']*'", '', page_str)
        
        new_pages.append(page_str)
            
    return new_pages

def process_image_scene_breaks(pages, image_map, doc_id, book_dir):
    import urllib.parse
    import re
    
    src_prefix = f"/api/library/image/{doc_id}/"
    symbol_map = {}
    src_counts = {}
    
    for page_html in pages:
        soup = BeautifulSoup(page_html, 'html.parser')
        for img in soup.find_all(['img', 'image']):
            src = img.get('src') or ''
            if src.startswith(src_prefix):
                src_counts[src] = src_counts.get(src, 0) + 1
                
    for src, count in src_counts.items():
        assigned_id = src.replace(src_prefix, "")
        filename = image_map.get(urllib.parse.unquote(assigned_id))
        if not filename: continue
        
        clues = re.split(r'[^a-zA-Z0-9]', filename.lower())
        is_symbolic = False
        shape = "●"
        
        if 'circle' in clues: shape = "●"
        elif 'box' in clues or 'square' in clues: shape = "■"
        elif 'star' in clues: shape = "★"
        elif 'diamond' in clues or 'orn' in clues: shape = "◆"
        elif 'triangle' in clues: shape = "▼"
        
        kw_match = any(kw in clues for kw in ['circle', 'box', 'square', 'star', 'break', 'line', 'ornament', 'orn', 'sep', 'div', 'divider', 'fleuron', 'diamond', 'decoration'])
        
        img_path = book_dir / filename
        w, h = get_image_size(img_path)
        
        symbol_count = 1
        
        if kw_match:
            is_symbolic = True
            nums = re.findall(r'\d+', filename)
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
            symbol_map[src] = "".join([shape] * symbol_count)
        elif w and h and w <= 150 and h <= 150 and count > 5:
            symbol_map[src] = "§§S_WRAP§§"
            
    if not symbol_map: return pages
    
    new_pages = []
    for page_html in pages:
        if not any(src in page_html for src in symbol_map):
            new_pages.append(page_html)
            continue
            
        soup = BeautifulSoup(page_html, 'html.parser')
        for img in soup.find_all(['img', 'image']):
            src = img.get('src') or ''
            if src in symbol_map:
                if img.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    continue
                    
                # Find highest empty wrapper to vaporize nested divs
                top_node = img
                curr = img.parent
                while curr and curr.name in ['div', 'p', 'figure', 'section', 'span', 'a', 'center', 'blockquote']:
                    if curr.get_text(strip=True) or len(curr.find_all(['img', 'image'])) > 1:
                        break
                    top_node = curr
                    curr = curr.parent
                    
                check_node = top_node
                
                prev_text_node = None
                for curr in check_node.find_all_previous(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span']):
                    if curr.get_text(strip=True):
                        prev_text_node = curr
                        break
                        
                if not prev_text_node or prev_text_node.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] or prev_text_node.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    continue

                next_text_node = None
                for curr in check_node.find_all_next(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span']):
                    if curr.get_text(strip=True):
                        next_text_node = curr
                        break
                        
                if not next_text_node or next_text_node.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] or next_text_node.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    continue

                chars = symbol_map[src]
                new_tag = soup.new_tag('s')
                
                # Nuke IDs to prevent <s> from stealing header targets
                if 'id' in img.attrs: del img['id']
                if 'data-orig-id' in img.attrs: del img['data-orig-id']
                
                if chars == "§§S_WRAP§§":
                    extracted_img = img.extract()
                    new_tag.append(extracted_img)
                    top_node.replace_with(new_tag)
                else:
                    new_tag.string = chars
                    if top_node != img:
                        top_node.replace_with(new_tag)
                    else:
                        new_tag.name = 'span'
                        img.replace_with(new_tag)
                        
        body = soup.find('body')
        page_str = str(body) if body else str(soup)
        page_str = re.sub(r'>\s*\n+\s*<', '><', page_str)
        new_pages.append(page_str)
        
    return new_pages


@router.post("/api/convert/epub")
async def convert_epub(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)): 
    import re
    from fastapi import HTTPException
    import html
    
    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Not an EPUB file")

    doc_id = id
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    temp_epub = book_dir / "temp.epub"

    try:
        with open(temp_epub, "wb") as f:
            content = await file.read()
            f.write(content)

        # ==========================================
        # 🌟 THE GHOST FILE MONKEY-PATCH SHIELD 🌟
        # ==========================================
        # ebooklib fatally crashes during read_epub() if the manifest 
        # lists a file that isn't actually inside the ZIP archive.
        # We temporarily hijack the internal read function to return empty bytes instead of crashing.
        original_read_file = epub.EpubReader.read_file

        def ghost_proof_read_file(self, name):
            try:
                return original_read_file(self, name)
            except KeyError:
                print(f"[Warning] EbookLib monkey-patch suppressed Ghost File: {name}")
                return b""

        epub.EpubReader.read_file = ghost_proof_read_file

        try:
            # We also pass ignore_ncx=True to shield against broken native TOC tables
            book = epub.read_epub(str(temp_epub), {'ignore_ncx': True})
        except Exception as e:
            shutil.rmtree(book_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Cannot read file (Corrupted or DRM): {e}")
        finally:
            # ALWAYS restore the original library function after reading so we don't break other processes!
            epub.EpubReader.read_file = original_read_file

        # 🌟 EXTRACT NATIVE TOC TITLES EARLY FOR HTML NORMALIZATION
        known_toc_titles = set()
        rich_toc_map = {}
        master_css = ""
        
        try:
            for item in book.get_items():
                if getattr(item, 'media_type', '') == 'text/css' or item.file_name.lower().endswith('.css'):
                    try:
                        master_css += item.get_content().decode('utf-8', errors='ignore') + "\n"
                    except Exception:
                        pass
        except Exception:
            pass
        
        try:
            # BRANCH 1: Native EPUB Metadata TOC (Bulletproofed & Sanitized)
            if hasattr(book, 'toc'):
                def extract_early_titles(items):
                    if not isinstance(items, (list, tuple)): return
                    for item in items:
                        try:
                            node = item[0] if isinstance(item, (tuple, list)) and len(item) == 2 else item
                            if hasattr(node, 'title') and node.title:
                                clean_title = " ".join(str(node.title).split()).lower()
                                known_toc_titles.add(clean_title)
                                
                                href = str(getattr(node, 'href', ''))
                                if href:
                                    import posixpath
                                    clean_href = posixpath.basename(href.split('#')[0]).lower()
                                    anchor = href.split('#')[1].lower() if '#' in href else ''
                                    if clean_href not in rich_toc_map:
                                        rich_toc_map[clean_href] = []
                                    rich_toc_map[clean_href].append({
                                        'title': str(node.title),
                                        'clean_title': clean_title,
                                        'anchor': anchor
                                    })
                            if isinstance(item, (tuple, list)) and len(item) == 2:
                                extract_early_titles(item[1])
                        except Exception:
                            pass
                extract_early_titles(book.toc)

            # BRANCH 2: Fallback to HTML TOC (nav.xhtml, toc.xhtml)
            if len(known_toc_titles) < 3:
                for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    name_lower = item.get_name().lower()
                    if any(x in name_lower for x in ['toc', 'nav', 'tableofcontents', 'contents']):
                        try:
                            toc_soup = BeautifulSoup(item.get_content().decode('utf-8', 'ignore'), 'html.parser')
                            for a_tag in toc_soup.find_all('a'):
                                title = a_tag.get_text(separator=" ", strip=True)
                                clean_title = " ".join(title.split()).lower()
                                if clean_title and len(clean_title) > 2 and not clean_title.isdigit():
                                    known_toc_titles.add(clean_title)
                        except Exception:
                            pass
        except Exception as e:
            # 🌟 ARMOR: If TOC extraction fails, it logs a warning but DOES NOT crash!
            print(f"[Warning] Early TOC Extractor encountered an error: {e}")

        pages = []
        image_map = {}
        extracted_images = set() # Replaces counter to track across all chapters
        global_sentence_idx = 0
        
        href_to_page = {}

        spine_tuples = getattr(book, 'spine', [])
        
        for spine_item in spine_tuples:
            item_id = spine_item[0]
            item = book.get_item_with_id(item_id)
            
            if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
                
            actual_href = item.get_name()
            raw_html = item.get_content().decode('utf-8', 'ignore')
            
            # 🌟 1. Pre-burn XML headers natively before Soup
            try:
                from logic.html_normalizer import pre_parse_clean, normalize_epub_html, standardize_footnotes
                raw_html = pre_parse_clean(raw_html)
            except Exception:
                pass
            
            soup = BeautifulSoup(raw_html, "html.parser")
            
            # 🌟 FOOTNOTE PRE-PROCESSOR SHIELD
            # Must run before formatting markers to fix publisher mis-tagging!
            try:
                standardize_footnotes(soup)
            except Exception as e:
                pass

            # ========== FORCE MARKERS AS EARLY AS POSSIBLE ==========
            force_formatting_markers(soup, actual_href)

            try:
                normalize_epub_html(soup, known_toc_titles=known_toc_titles, current_href=actual_href, rich_toc_map=rich_toc_map)
            except Exception as e:
                print(f"[Warning] HTML Normalizer failed: {e}")

            # Re-apply markers in case normalize_epub_html destroyed them
            force_formatting_markers(soup, actual_href)
               
            html_dir = posixpath.dirname(item.get_name())
            
            for img in soup.find_all(['img', 'image']):
                if img.parent is None:
                    continue

                src = img.get('src') or img.get('xlink:href') or img.get('href')
                if not src:
                    svg_wrapper = img.find_parent('svg')
                    if svg_wrapper: svg_wrapper.decompose()
                    else: img.decompose()
                    continue

                src = src.split('#')[0]
                resolved_href = urllib.parse.unquote(posixpath.normpath(posixpath.join(html_dir, src))).lstrip('/')
                
                # Try Engine 1: Standard EbookLib Lookup
                image_item = book.get_item_with_href(resolved_href)
                if not image_item:
                    search_href = resolved_href.lower()
                    for i in book.get_items():
                        if i.get_name().lower() == search_href:
                            image_item = i
                            break
                            
                if not image_item:
                    search_basename = posixpath.basename(resolved_href).lower()
                    if search_basename:
                        for i in book.get_items():
                            if posixpath.basename(i.get_name()).lower() == search_basename:
                                image_item = i
                                break

                img_content = None
                actual_item_name = None

                # Engine 1 Extraction Attempt
                if image_item:
                    try:
                        img_content = image_item.get_content()
                        actual_item_name = image_item.get_name()
                    except Exception as e:
                        print(f"[Warning] Manifested Ghost file skipped: {image_item.get_name()}")
                        img_content = None

                # ==========================================
                # 🌟 ENGINE 2: THE RAW ZIP BYPASS SHIELD
                # ==========================================
                # If EbookLib couldn't find the file because the publisher forgot to list it 
                # in the manifest, we crack open the raw ZIP archive and extract it by force.
                if not img_content:
                    search_basename = posixpath.basename(resolved_href)
                    if search_basename:
                        try:
                            import zipfile
                            with zipfile.ZipFile(str(temp_epub), 'r') as z:
                                match_path = None
                                # Scan the raw directory tree of the zip file
                                for zinfo in z.infolist():
                                    if posixpath.basename(zinfo.filename) == search_basename:
                                        match_path = zinfo.filename
                                        break
                                
                                if match_path:
                                    img_content = z.read(match_path)
                                    actual_item_name = match_path
                                    print(f"[Info] Rescued unmanifested image via Raw ZIP Engine: {match_path}")
                        except Exception as e:
                            print(f"[Warning] Raw ZIP Engine failed for {search_basename}: {e}")

                # ==========================================
                # SAVE & SANITIZE PHASE
                # ==========================================
                if img_content and actual_item_name:
                    clean_name = actual_item_name.split('?')[0].split('#')[0]
                    base_name = posixpath.splitext(clean_name)[0]
                    ext = posixpath.splitext(clean_name)[1].lower()
                    
                    safe_base = re.sub(r'[\\/*?:"<>|]', "", base_name.replace('/', '_').replace('\\', '_'))
                    
                    if len(safe_base) > 50:
                        import uuid
                        safe_base = safe_base[:40] + "_" + uuid.uuid4().hex[:6]
                    elif not safe_base:
                        import uuid
                        safe_base = f"img_{uuid.uuid4().hex[:8]}"

                    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']: 
                        ext = ".jpg"
                        
                    safe_filename = f"{safe_base}{ext}"
                        
                    if actual_item_name not in extracted_images:
                        image_path = book_dir / safe_filename
                        try:
                            with open(image_path, "wb") as img_file:
                                img_file.write(img_content)
                            
                            image_map[safe_filename] = safe_filename
                            extracted_images.add(actual_item_name)
                        except Exception as e:
                            print(f"[Warning] Failed to save image {safe_filename} to disk: {e}")
                    
                    assigned_id = urllib.parse.quote(safe_filename)

                    new_img = soup.new_tag('img')
                    new_img['src'] = f"/api/library/image/{doc_id}/{assigned_id}"
                    new_img['class'] = "epub-image"
                    new_img['loading'] = "lazy"
                    
                    svg_wrapper = img.find_parent('svg')
                    if svg_wrapper:
                        svg_wrapper.replace_with(new_img)
                    else:
                        img.replace_with(new_img)
                else:
                    # Only delete the tag if BOTH engines completely failed to find the bytes
                    svg_wrapper = img.find_parent('svg')
                    if svg_wrapper:
                        svg_wrapper.decompose()
                    else:
                        img.decompose()

            for p in soup.find_all(['p', 'div']):
                if not p.find('img'):
                    p_text = p.get_text(strip=True)
                    chars = [c for c in p_text if not c.isspace()]
                    if not chars: continue
                    
                    length = len(chars)
                    if length > 20: continue
                        
                    if re.search(r'[a-zA-Z0-9\u00C0-\u00FF\u0400-\u04FF\u3041-\u3096\u30A1-\u30FA\u4E00-\u9FAF\uAC00-\uD7AF]', p_text):
                        continue
                        
                    forbidden_punctuation = set(".,!?:;\"'“”‘’「」『』()[]{}<>。、・？！…")
                    if any(c in forbidden_punctuation for c in chars):
                        continue
                        
                    is_scene_break = False
                    if length >= 2: is_scene_break = True
                    elif length == 1:
                        valid_singles = set("*#-_~♦◇◆○●■□▼▽★☆❖✦⁂※†—–―─●")
                        if chars[0] in valid_singles:
                            is_scene_break = True
                            
                    if is_scene_break:
                        sb = soup.new_tag('s')
                        sb.string = p_text
                        p.replace_with(sb)

            for block in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'figure']):
                if block.find(['p', 'div', 'ul', 'ol', 'table', 'blockquote', 'figure', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    continue
                    
                has_media = block.find(['img', 's', 'picture', 'svg', 'figure'])
                if has_media:
                    # 🌟 THE IMAGE HEADER INTERCEPTOR 🌟
                    if block.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                        orig_id = block.get('id')
                        for a_tag in block.find_all('a'):
                            if not orig_id and a_tag.get('id'):
                                orig_id = a_tag.get('id')
                            a_tag.unwrap()
                        junk_attrs = ['class', 'style', 'lang', 'dir']
                        for attr in list(block.attrs):
                            if attr.lower() in junk_attrs:
                                del block.attrs[attr]
                        block['id'] = f's_{global_sentence_idx}'
                        if orig_id:
                            block['data-orig-id'] = orig_id
                        global_sentence_idx += 1
                        continue
                        
                    # 🌟 MEDIA-TEXT MIX RESCUE 🌟
                    # If purely media with zero text, bypass splitter so JS gets a raw block
                    if not block.get_text(strip=True):
                        continue

                # Media protection ONLY
                shield_map = {}
                for shield in list(block.find_all(['img', 's', 'picture', 'svg', 'figure'])):
                    s_id = f"§§SHIELD{len(shield_map)}§§"
                    shield_map[s_id] = str(shield)
                    shield.replace_with(s_id)

                text = block.get_text(separator=" ", strip=False)
                text = re.sub(r'[ \t\r\f\v]+', ' ', text)
                text = re.sub(r' *\n+ *', ' ', text).strip()
                text = re.sub(r'\s+(§§SHIELD\d+§§)', r'\1', text)

                if text.replace('§§BR§§', '').strip() == '':
                    block.clear()
                    for _ in range(text.count('§§BR§§')):
                        block.append(BeautifulSoup('<br/>', 'html.parser'))
                    continue
                if not text:
                    continue

                safe_text = html.escape(text)

                if block.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    orig_id = block.get('id')
                    if not orig_id:
                        inner_tag = block.find(id=True)
                        if inner_tag:
                            orig_id = inner_tag.get('id')
                            
                    block.clear()
                    block['id'] = f's_{global_sentence_idx}'
                    if orig_id:
                        block['data-orig-id'] = orig_id
                        
                    header_html = (
                        safe_text
                        .replace('§§B_ON§§', '<b>').replace('§§B_OFF§§', '</b>')
                        .replace('§§I_ON§§', '<i>').replace('§§I_OFF§§', '</i>')
                        .replace('§§U_ON§§', '<u>').replace('§§U_OFF§§', '</u>')
                        .replace('§§D_ON§§', '<del>').replace('§§D_OFF§§', '</del>')
                        .replace(' §§BR§§ ', '<br/>').replace('§§BR§§', '<br/>')
                        .replace('§§F_ON§§ ', '').replace('§§F_ON§§', '')
                        .replace(' §§F_OFF_A§§', '</a>').replace('§§F_OFF_A§§', '</a>')
                        .replace(' §§F_OFF_SUP§§', '</sup></a>').replace('§§F_OFF_SUP§§', '</sup></a>')
                    )
                    header_html = re.sub(r'§§F_S\|([^§|]*)\|SUP§§\s*', r'<a epub:type="noteref" href="#\1"><sup>', header_html)
                    header_html = re.sub(r'§§F_S\|([^§|]*)\|A§§\s*', r'<a epub:type="noteref" href="#\1">', header_html)
                    header_html = re.sub(r'§§F_E\|([^§|]*)§§\s*', r'<a epub:type="footnote" id="\1">', header_html)
                    
                    for s_id, s_html in shield_map.items():
                        header_html = header_html.replace(s_id, s_html)
                    block.append(BeautifulSoup(header_html, 'html.parser'))
                    global_sentence_idx += 1
                    continue

                new_html, global_sentence_idx = master_sentence_splitter(safe_text, global_sentence_idx)

                if new_html:
                    for s_id, s_html in shield_map.items():
                        new_html = new_html.replace(s_id, s_html)
                    block.clear()
                    block.append(BeautifulSoup(new_html, 'html.parser'))
                else:
                    block.clear()

            for block in soup.find_all(['div', 'p', 'figure', 'span']):
                if not block.get_text(strip=True) and not block.find(['img', 'hr', 'br', 'svg', 'picture', 's', 'n']):
                    block.decompose()

            body = soup.find('body')
            page_html = str(body) if body else str(soup)
            
            # Minify: Eliminate all linebreaks and whitespace exactly between closing and opening tags
            page_html = re.sub(r'>\s*\n+\s*<', '><', page_html)
            
            # 🌟 THE GLOBAL CLEANUP SWEEP 🌟
            # Revert any formatting markers left behind inside shielded image blocks
            page_html = (
                page_html
                .replace('§§BR§§', '<br/>')
                .replace('§§B_ON§§', '<b>').replace('§§B_OFF§§', '</b>')
                .replace('§§I_ON§§', '<i>').replace('§§I_OFF§§', '</i>')
                .replace('§§U_ON§§', '<u>').replace('§§U_OFF§§', '</u>')
                .replace('§§D_ON§§', '<del>').replace('§§D_OFF§§', '</del>')
            )
            
            if "<n id=" in page_html or "<img" in page_html or "<s>" in page_html:
                href_to_page[actual_href] = len(pages)
                pages.append(page_html)
                
        # 🌟 THE FALSE-POSITIVE IMAGE HEADER REVOKER 🌟
        # Demotes Chapter Art ONLY if the exact TOC text appears in a real header on the following page.
        for i in range(len(pages)):
            if '<h1' in pages[i] and ('<img' in pages[i] or '<image' in pages[i]):
                current_soup = BeautifulSoup(pages[i], 'html.parser')
                modified = False
                for h in current_soup.find_all('h1'):
                    img = h.find(['img', 'image'])
                    if img:
                        text_nodes = "".join(h.stripped_strings)
                        hidden = h.find('span', class_='epub-visually-hidden')
                        hidden_text = hidden.get_text(strip=True) if hidden else ""
                        
                        # 🌟 STRICT SHIELD: Must have hidden TOC text to verify duplication
                        if hidden_text and text_nodes == hidden_text:
                            found_duplicate = False
                            
                            for lookahead in range(1, 3):
                                if i + lookahead < len(pages):
                                    next_soup = BeautifulSoup(pages[i + lookahead], 'html.parser')
                                    real_headers = [nx.get_text(strip=True) for nx in next_soup.find_all(['h1', 'h2', 'h3']) if nx.get_text(strip=True)]
                                    
                                    if real_headers:
                                        hidden_lower = re.sub(r'[^\w]', '', hidden_text.lower())
                                        for rh in real_headers:
                                            rh_lower = re.sub(r'[^\w]', '', rh.lower())
                                            if hidden_lower and rh_lower and (hidden_lower in rh_lower or rh_lower in hidden_lower):
                                                found_duplicate = True
                                                break
                                                
                                        # Stop looking. If headers didn't match the TOC string, DO NOT REVOKE.
                                        break
                                        
                                    if "".join(next_soup.stripped_strings):
                                        break 
                            
                            if found_duplicate:
                                h.name = 'p'
                                if hidden: hidden.decompose()
                                modified = True
                
                if modified:
                    body = current_soup.find('body')
                    page_str = str(body) if body else str(current_soup)
                    page_str = re.sub(r'>\s*\n+\s*<', '><', page_str)
                    pages[i] = page_str
                
        pages = process_css_scene_breaks(pages, master_css)
        pages = process_image_scene_breaks(pages, image_map, doc_id, book_dir)
     
        
        # 🌟 FIX: Stop Windows WinError 32 from crashing the finish line
        try:
            temp_epub.unlink(missing_ok=True)
        except Exception as e:
            print(f"[Warning] Windows locked temp.epub, cleanup deferred: {e}")
        
        def parse_native_toc(items, level=1):
            res = []
            if not items: return res
            
            for item in items:
                try:
                    if isinstance(item, (tuple, list)):
                        if len(item) == 2 and hasattr(item[0], 'title'):
                            section = item[0]
                            children = item[1]
                            href = getattr(section, 'href', '') or ''
                            href_str = str(href)
                            clean_href = href_str.split('#')[0]
                            anchor_id = href_str.split('#')[1] if '#' in href_str else None
                            
                            idx = href_to_page.get(clean_href, -1)
                            if idx == -1:
                                for h, p in href_to_page.items():
                                    if posixpath.basename(h) == posixpath.basename(clean_href):
                                        idx = p
                                        break
                                        
                            if idx != -1:
                                title_str = str(getattr(section, 'title') or f"Chapter (Page {idx + 1})")
                                res.append({"title": title_str, "level": level, "page_index": idx, "anchor_id": anchor_id})
                                
                            res.extend(parse_native_toc(children, level + 1))
                        else:
                            res.extend(parse_native_toc(item, level))
                    elif hasattr(item, 'title') and hasattr(item, 'href'):
                        href = getattr(item, 'href', '') or ''
                        href_str = str(href)
                        clean_href = href_str.split('#')[0]
                        anchor_id = href_str.split('#')[1] if '#' in href_str else None
                        
                        idx = href_to_page.get(clean_href, -1)
                        if idx == -1:
                            for h, p in href_to_page.items():
                                if posixpath.basename(h) == posixpath.basename(clean_href):
                                    idx = p
                                    break
                                    
                        if idx != -1:
                            title_str = str(getattr(item, 'title') or f"Chapter (Page {idx + 1})")
                            res.append({"title": title_str, "level": level, "page_index": idx, "anchor_id": anchor_id})
                except Exception:
                    # Automatically skip malformed .ncx items without crashing the pipeline
                    continue
            return res

        toc_map = []
        try:
            if hasattr(book, 'toc') and book.toc:
                toc_map = parse_native_toc(book.toc)
        except Exception as e:
            print(f"[Warning] Native TOC parser failed: {e}")
            
        if not toc_map:
            toc_map = generate_toc(pages)

        # 🌟 TOC SYNCHRONIZER 🌟
        # Links directly to html_normalizer. No redundant text-guessing required.
        claimed_ids = set()

        for toc_item in toc_map:
            p_idx = toc_item.get('page_index', 0)
            if p_idx < 0 or p_idx >= len(pages):
                continue
                
            page_soup = BeautifulSoup(pages[p_idx], 'html.parser')
            target_tts_id = None
            
            # Step 1: Trust the exact anchor provided by the metadata map (if any)
            anchor = toc_item.get('anchor_id')
            if anchor:
                clean_anchor = anchor.split('#')[-1]
                # Look for data-orig-id (salvaged by normalizer) or standard id
                el = page_soup.find(attrs={"data-orig-id": clean_anchor}) or page_soup.find(id=clean_anchor)
                if el:
                    if el.get('id', '').startswith('s_'):
                        target_tts_id = el.get('id')
                    else:
                        child = el.find(id=re.compile(r'^s_'))
                        if child: target_tts_id = child.get('id')
                            
            # Step 2: Trust the Normalizer Pipeline (If no anchor or anchor failed)
            # The normalizer mathematically guaranteed that the TOC target is now an H1 or H2.
            # If it's a Good EPUB with an image at the top, it skips the image and finds the native H1.
            if not target_tts_id:
                for first_h in page_soup.find_all(['h1', 'h2', 'h3']):
                    h_id = first_h.get('id')
                    if h_id and h_id.startswith('s_') and h_id not in claimed_ids:
                        target_tts_id = h_id
                        break
                    
            # Step 3: Absolute fallback to the first spoken sentence on the page
            if not target_tts_id:
                for first_n in page_soup.find_all(id=re.compile(r'^s_')):
                    n_id = first_n.get('id')
                    if n_id and n_id not in claimed_ids:
                        target_tts_id = n_id
                        break
                        
            if target_tts_id:
                claimed_ids.add(target_tts_id)
                
            toc_item['target_tts_id'] = target_tts_id

        return {
            "pages": pages,
            "image_map": image_map,
            "toc_map": toc_map
        }
    except Exception as e:
        import traceback
        print("\n" + "="*60)
        print("🚨 FATAL EPUB EXTRACTION CRASH 🚨")
        traceback.print_exc()
        print("="*60 + "\n")
        
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/api/convert/pdf")
async def convert_pdf(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    import shutil
    try:
        import fitz
    except ImportError:
        raise HTTPException(status_code=500, detail="PyMuPDF library not installed.")
        
    from fastapi import HTTPException
    # Removed the legacy split_pdf_sentences import. Master splitter does it now!
    from logic.smart_content_detector import detect_strict_scene_break
    from logic.html_normalizer import generate_toc

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Not a PDF file")

    doc_id = id
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    temp_pdf = book_dir / "temp.pdf"

    try:
        with open(temp_pdf, "wb") as f:
            content = await file.read()
            f.write(content)

        try:
            doc = fitz.open(str(temp_pdf))
        except Exception:
            shutil.rmtree(book_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Cannot read PDF file (corrupted or DRM protected)")

        total_text_len = sum(len(doc[i].get_text()) for i in range(min(5, len(doc))))
        if len(doc) > 0 and total_text_len < 50:
            raise HTTPException(status_code=400, detail="Scanned (image-only) PDFs are not supported for TTS. Please provide a text-based PDF.")

        raw_toc = doc.get_toc()
        toc_map = []
        if raw_toc:
            for item in raw_toc:
                lvl, title, page_num = item
                toc_map.append({"title": title, "level": lvl, "page_index": max(0, page_num - 1), "anchor_id": None})

        allow_scene_breaks = False
        if len(doc) > 0:
            first_page_images = doc[0].get_images(full=True)
            if first_page_images:
                allow_scene_breaks = True

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

            for element in elements:
                if element["type"] == "text":
                    block = element["block"]
                    block_text = ""
                    max_fontsize = 0
                    
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            block_text += span["text"] + " "
                            if span["size"] > max_fontsize:
                                max_fontsize = span["size"]
                    
                    block_text = " ".join(block_text.split()).strip()
                    block_text = block_text.replace('\uf0b7', '').replace('\uf020', '').strip()
                    if block_text.startswith('•'): block_text = block_text[1:].strip()
                        
                    if not block_text or block_text in ['•', '-', '·']: continue

                    is_header = False
                    if max_fontsize > 14 and len(block_text) < 100 and not block_text.endswith(paragraph_terminators):
                        is_header = True
                        
                    is_scene_break = detect_strict_scene_break(block_text, allow_scene_breaks)

                    if (is_header or is_scene_break) and held_text:
                        # 🌟 UNIFIED SPLIT FIX 🌟
                        sentences_html, global_sentence_idx = master_sentence_splitter(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>"
                        held_text = ""

                    if not is_header and not is_scene_break and held_text:
                        if held_text.endswith("-") and not held_text.endswith(" -"):
                            block_text = held_text[:-1] + block_text
                        else:
                            block_text = held_text + " " + block_text
                        held_text = ""

                    if not is_header and not is_scene_break and not block_text.endswith(paragraph_terminators):
                        held_text = block_text
                        continue

                    if is_scene_break:
                        page_html += f"<s>{block_text}</s>"
                    elif is_header:
                        import html
                        safe_header = html.escape(block_text)
                        # Added id directly to header, removed trailing newlines
                        page_html += f'<h2 id="s_{global_sentence_idx}">{safe_header}</h2>'
                        global_sentence_idx += 1
                    else:
                        sentences_html, global_sentence_idx = master_sentence_splitter(block_text, global_sentence_idx)
                        if sentences_html:
                            page_html += f"<p>{sentences_html}</p>"

                elif element["type"] == "image":
                    if held_text:
                        sentences_html, global_sentence_idx = master_sentence_splitter(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>"
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
                        
                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)
                            
                        image_map[str(image_counter)] = image_filename
                        assigned_id = str(image_counter)
                        image_counter += 1
                        page_html += f'<img src="/api/library/image/{doc_id}/{assigned_id}" class="epub-image" loading="lazy" style="max-width:100%; height:auto;" />'
                    except Exception: pass

                elif element["type"] == "table":
                    if held_text:
                        sentences_html, global_sentence_idx = master_sentence_splitter(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>"
                        held_text = ""
                        
                    table_html = "<table class='pdf-table' border='1' style='border-collapse: collapse; width: 100%; margin: 10px 0;'>"
                    for row in element["data"]:
                        table_html += "<tr>"
                        for cell in row:
                            cell_text = str(cell) if cell else ""
                            if cell_text.strip():
                                chunk, global_sentence_idx = master_sentence_splitter(cell_text.strip(), global_sentence_idx)
                                table_html += f"<td style='padding: 6px;'>{chunk}</td>"
                            else:
                                table_html += "<td></td>"
                        table_html += "</tr>"
                    table_html += "</table>"
                    page_html += table_html

            if page_html.strip():
                pages.append(f'<div class="pdf-page">{page_html}</div>')
            else:
                pages.append(f'<div class="pdf-page"><p><n id="s_{global_sentence_idx}">[Blank Page]</n></p></div>')
                global_sentence_idx += 1

        if held_text:
            sentences_html, global_sentence_idx = master_sentence_splitter(held_text, global_sentence_idx)
            if sentences_html:
                if pages:
                    pages[-1] = pages[-1].replace('</div>', f'<p>{sentences_html}</p></div>')
                else:
                    pages.append(f'<div class="pdf-page"><p>{sentences_html}</p></div>')

        doc.close()
        temp_pdf.unlink(missing_ok=True)
        
        if not toc_map:
            toc_map = generate_toc(pages)

        claimed_ids = set()

        for toc_item in toc_map:
            p_idx = toc_item.get('page_index', 0)
            if p_idx < 0 or p_idx >= len(pages):
                continue
                
            page_soup = BeautifulSoup(pages[p_idx], 'html.parser')
            target_tts_id = None
            
            anchor = toc_item.get('anchor_id')
            if anchor:
                clean_anchor = anchor.split('#')[-1]
                el = page_soup.find(attrs={"data-orig-id": clean_anchor}) or page_soup.find(id=clean_anchor)
                if el and el.get('id', '').startswith('s_'):
                    target_tts_id = el.get('id')
                elif el:
                    child = el.find(id=re.compile(r'^s_'))
                    if child: target_tts_id = child.get('id')
                    
            if not target_tts_id and toc_item.get('title'):
                clean_title = re.sub(r'[^\w]', '', toc_item['title'].lower())
                for h in page_soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    h_text = re.sub(r'[^\w]', '', h.get_text().lower())
                    if clean_title and (clean_title in h_text or h_text in clean_title):
                        h_id = h.get('id')
                        if h_id and h_id.startswith('s_') and h_id not in claimed_ids:
                            target_tts_id = h_id
                            break
                            
            if not target_tts_id:
                for first_h in page_soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    h_id = first_h.get('id')
                    if h_id and h_id.startswith('s_') and h_id not in claimed_ids:
                        target_tts_id = h_id
                        break
                    
            if not target_tts_id:
                for first_n in page_soup.find_all(id=re.compile(r'^s_')):
                    n_id = first_n.get('id')
                    if n_id and n_id not in claimed_ids:
                        target_tts_id = n_id
                        break
                        
            if target_tts_id:
                claimed_ids.add(target_tts_id)
                
            toc_item['target_tts_id'] = target_tts_id

        return {
            "pages": pages,
            "image_map": image_map,
            "toc_map": toc_map
        }

    except Exception as e:
        import traceback
        print("\n" + "="*60)
        print("🚨 FATAL PDF EXTRACTION CRASH 🚨")
        traceback.print_exc()
        print("="*60 + "\n")
        
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/library")
def get_library():
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

@router.post("/api/library")
async def save_library_item(item: LibraryItem):
    async with _library_lock:
        try:
            with open(library_file, "r", encoding="utf-8") as f:
                library = json.load(f)
        except Exception:
            library = []

        found = False
        for i, existing in enumerate(library):
            if existing.get("id") == item.id:
                library[i] = item.model_dump()
                found = True
                break
        if not found:
            library.append(item.model_dump())

        safe_save_json(library_file, library)
        return {"status": "ok"}

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
                if book_dir.exists():
                    shutil.rmtree(book_dir, ignore_errors=True)
                for ext in [".json", ".pdf", ".epub"]:
                    file_path = content_dir / f"{doc_id}{ext}"
                    if file_path.exists():
                        try:
                            file_path.unlink()
                        except Exception:
                            pass
                return {"status": "deleted"}
            else:
                raise HTTPException(status_code=404, detail="Document not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/library/content/{doc_id}")
def get_content(doc_id: str):
    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["smart_start_page"] = 0
    return data

@router.post("/api/library/content")
async def save_content(request: Request):
    data = await request.json()
    doc_id = data['id']
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    safe_save_json(book_dir / f"{doc_id}.json", data)
    return {"status": "ok"}

@router.get("/api/library/image/{doc_id}/{image_id}")
def get_image(doc_id: str, image_id: str):
    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    image_map = data.get("image_map", {})
    filename = image_map.get(image_id)

    if not filename: raise HTTPException(status_code=404, detail="Image not mapped")
    image_path = content_dir / doc_id / filename
    if not image_path.exists(): raise HTTPException(status_code=404, detail="Image missing")

    return FileResponse(image_path)

@router.get("/api/library/content/{doc_id}/page/{page_index}")
def get_page_with_filter(doc_id: str, page_index: int):
    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pages = data.get("pages", [])
    if page_index < 0 or page_index >= len(pages):
        raise HTTPException(status_code=400, detail="Invalid page index")

    with open(settings_file, "r", encoding="utf-8") as f:
        settings = json.load(f)

    mode = settings.get("header_footer_mode", "off")
    page_text = pages[page_index]

    noise = detect_headers_footers(pages, page_index)
    if mode in ["clean", "dim"]:
        filtered_text = apply_header_footer_filter(page_text, noise["headers"], noise["footers"], mode)
    else:
        filtered_text = page_text

    return {
        "page_index": page_index, "original_text": page_text, "filtered_text": filtered_text,
        "headers": noise["headers"], "footers": noise["footers"], "mode": mode,
    }

@router.get("/api/library/search/{doc_id}")
def search_book(doc_id: str, q: str, match_case: bool = False, whole_word: bool = False):
    if not q or len(q) < 2: return {"results": [], "total_matches": 0, "query": q}

    file_path = get_doc_json_path(doc_id)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pages = data.get("pages", [])
    results = []
    total_matches = 0
    
    q_norm = q.replace('‘', "'").replace('’', "'").replace('´', "'").replace('`', "'").replace('“', '"').replace('”', '"')
    flags = 0 if match_case else re.IGNORECASE
    escaped_q = re.escape(q_norm).replace("'", r"['‘’´`]").replace('"', r'["“”]')
    pattern_str = rf"\b{escaped_q}\b" if whole_word else escaped_q
    
    try: pattern = re.compile(pattern_str, flags)
    except Exception: return {"results": [], "total_matches": 0, "query": q}

    for page_index, page_html in enumerate(pages):
        soup = BeautifulSoup(page_html, "html.parser")
        page_text = soup.get_text(separator=" ")
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

@router.post("/api/library/progress/{doc_id}")
async def update_book_progress_checkpoint(doc_id: str, payload: ProgressUpdatePayload):
    if not library_file.exists():
        raise HTTPException(status_code=404, detail="Library inventory log absent.")

    async with _library_lock:
        try:
            with open(library_file, "r", encoding="utf-8") as f:
                books_inventory = json.load(f)
                
            target_book = next((book for book in books_inventory if book.get("id") == doc_id), None)

            if not target_book:
                raise HTTPException(status_code=404, detail="Requested record entry missing.")

            target_book["currentPage"] = payload.currentPage
            target_book["lastSentenceId"] = payload.lastSentenceId
            target_book["lastSentenceIndex"] = payload.lastSentenceIndex
            target_book["lastAccessed"] = payload.lastAccessed

            temp_lib_path = library_file.with_suffix(".tmp")
            with open(temp_lib_path, "w", encoding="utf-8") as write_handle:
                json.dump(books_inventory, write_handle, indent=4, ensure_ascii=False)
            temp_lib_path.replace(library_file)
            
        except Exception as io_error:
            print(f"[Error] Failed to auto-save progress to library.json: {io_error}")
            raise HTTPException(status_code=500, detail=f"Database sync failure: {str(io_error)}")

    return {"status": "success", "message": f"Checkpoint saved for {doc_id}"}