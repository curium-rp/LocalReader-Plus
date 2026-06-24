"""
Smart Content Detection Module for LocalReader plus 
Handles Smart Start (intro skip) and Header/Footer filtering.
and more we do more smarter then original one 
"""

import re
from typing import List, Tuple, Dict
from difflib import SequenceMatcher

def clean_epub_html(page_html: str) -> str:
    """
    Deep cleaner for EPUB HTML. Strips bad tags, unwraps spans, removes native TOCs via link density.
    """
    import re
    # 0. THE PRE-BURNER: Vaporize XML declarations and DOCTYPEs before soup parsing
    page_html = re.sub(r'<\?xml.*?\?>', '', page_html, flags=re.IGNORECASE | re.DOTALL)
    page_html = re.sub(r'<!DOCTYPE.*?>', '', page_html, flags=re.IGNORECASE | re.DOTALL)

    soup = BeautifulSoup(page_html, 'html.parser')

    # 1. THE TOC SNIPER (Strict Keyword + Link Density Check)
    links = soup.find_all('a')
    if links:
        link_text_len = sum(len(a.get_text(strip=True)) for a in links)
        text_content = soup.get_text(strip=True)
        text_lower = text_content.lower()
        
        # STRICT CHECK: Ensure the page actually claims to be a TOC
        is_toc_page = "table of contents" in text_lower or "contents" in text_lower or "toc" in text_lower.split()
        
        # If it claims to be a TOC, AND > 40% of the text is a hyperlink, AND has > 3 links... Vaporize it.
        if is_toc_page and len(text_content) > 0 and (link_text_len / len(text_content)) > 0.4 and len(links) > 3:
            return ""

    # 2. The Exterminator: Remove malicious/useless tags
    for tag in soup.find_all(['script', 'style', 'meta', 'iframe']):
        tag.decompose()

    # 3. The Unwrapper: Keep text, destroy inline styling and remaining hyperlinks
    for tag in soup.find_all(['span', 'a']):
        tag.unwrap()

    # 4. The Vacuum: Remove empty paragraphs/divs (no text and no images inside)
    for block in soup.find_all(['p', 'div', 'section', 'figure', 'main']):
        if not block.get_text(strip=True) and not block.find(['img', 'image', 'svg', 'picture']):
            block.decompose()

    # 5. The Fallback: Convert lazy text-heavy DIVs into P tags for uniform CSS
    for div in soup.find_all('div'):
        if div.get_text(strip=True) and not div.find(['p', 'div']):
            div.name = 'p'

    return str(soup)


def split_into_lines(text: str) -> List[str]:
    return [line.strip() for line in text.split('\n') if line.strip()]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_page_number(line: str) -> bool:
    cleaned = line.strip().replace('Page', '').replace('page', '').strip()
    if re.match(r'^[0-9]+$', cleaned):
        return True
    if re.match(r'^[ivxlcdm]+$', cleaned, re.IGNORECASE):
        return True
    if re.match(r'^\d+\s*of\s*\d+$', cleaned, re.IGNORECASE):
        return True
    return False


def detect_headers_footers(pages: List[str], page_index: int) -> Dict[str, List[str]]:
    if not pages or page_index >= len(pages):
        return {'headers': [], 'footers': []}
    
    current_page = pages[page_index]
    current_lines = split_into_lines(current_page)
    
    if len(current_lines) < 3:
        return {'headers': [], 'footers': []}
    
    headers = []
    footers = []
    
    prev_page = pages[page_index - 1] if page_index > 0 else None
    next_page = pages[page_index + 1] if page_index < len(pages) - 1 else None
    
    prev_lines = split_into_lines(prev_page) if prev_page else []
    next_lines = split_into_lines(next_page) if next_page else []
    
    limit = max(1, min(3, int(len(current_lines) * 0.2)))
    
    for i in range(limit):
        current_line = current_lines[i]
        matches = 0
        if prev_lines and i < len(prev_lines):
            if similarity(current_line, prev_lines[i]) > 0.9:
                matches += 1
        if next_lines and i < len(next_lines):
            if similarity(current_line, next_lines[i]) > 0.9:
                matches += 1
        if matches >= 1:
            headers.append(current_line)
            
    start_footer_scan = max(limit, len(current_lines) - limit)
    
    for i in range(start_footer_scan, len(current_lines)):
        current_line = current_lines[i]
        matches = 0
        offset_from_end = len(current_lines) - i - 1
        
        if prev_lines:
            prev_index = len(prev_lines) - offset_from_end - 1
            if 0 <= prev_index < len(prev_lines):
                if similarity(current_line, prev_lines[prev_index]) > 0.9:
                    matches += 1
        if next_lines:
            next_index = len(next_lines) - offset_from_end - 1
            if 0 <= next_index < len(next_lines):
                if similarity(current_line, next_lines[next_index]) > 0.9:
                    matches += 1
        if is_page_number(current_line):
            matches += 2
        
        if matches >= 1:
            footers.append(current_line)
            
    return {'headers': headers, 'footers': footers}


def apply_header_footer_filter(text: str, headers: List[str], footers: List[str], mode: str = 'clean') -> str:
    lines = split_into_lines(text)
    
    if mode == 'clean':
        filtered_lines = []
        for line in lines:
            is_noise = False
            for header in headers:
                if similarity(line, header) > 0.9:
                    is_noise = True
                    break
            if not is_noise:
                for footer in footers:
                    if similarity(line, footer) > 0.9:
                        is_noise = True
                        break
            if is_page_number(line):
                is_noise = True
            
            if not is_noise:
                filtered_lines.append(line)
        return '\n'.join(filtered_lines)
        
    elif mode == 'dim':
        marked_lines = []
        for line in lines:
            is_noise = False
            for header in headers:
                if similarity(line, header) > 0.9:
                    is_noise = True
                    break
            if not is_noise:
                for footer in footers:
                    if similarity(line, footer) > 0.9:
                        is_noise = True
                        break
            if is_page_number(line):
                is_noise = True
            
            if is_noise:
                marked_lines.append(f'[DIM]{line}[/DIM]')
            else:
                marked_lines.append(line)
        return '\n'.join(marked_lines)
        
    return text


def filter_text_for_tts(text: str) -> str:
    """
    Strips non-spoken formatting markers out of the text before TTS engine ingestion.
    CRITICAL FIX: We no longer delete <s> (Scene) or [IMAGE] markers here!
    If we delete them here, the frontend media player skips them entirely.
    """
    text = re.sub(r'\[DIM\].*?\[/DIM\]', '', text, flags=re.DOTALL)
    
    # Clean up standard Headers brackets but KEEP the text inside
    text = re.sub(r'\[/?H[1-6]\]', '', text)
    
    # Do NOT run the re.sub for <s> or IMAGE! Let the frontend intercept them.
    return text.strip()

from bs4 import BeautifulSoup

def generate_toc(pages):
    """
    Scans the pre-baked HTML pages for header tags and builds a Table of Contents map.
    """
    toc_map = []
    for page_index, page_html in enumerate(pages):
        soup = BeautifulSoup(page_html, 'html.parser')
        
        # 1. Native H1 to H3 scan (Your primary logic)
        for header in soup.find_all(['h1', 'h2', 'h3']):
            title = header.get_text(strip=True)
            if title and len(title) < 100: # Prevent accidental massive paragraphs
                level = int(header.name[1]) 
                # Avoid inserting exact duplicates for the same page
                if not any(t['page_index'] == page_index and t['title'] == title for t in toc_map):
                    toc_map.append({"title": title, "level": level, "page_index": page_index})
                    
        # 2. Smart Fallback: Catch lazy authors who used <p> tags for chapters
        for p in soup.find_all(['p', 'div']):
            text = p.get_text(strip=True)
            # If a line explicitly starts with "Chapter X", grab it
            if text.lower().startswith('chapter ') and len(text) < 50:
                if not any(t['page_index'] == page_index and t['title'] == text for t in toc_map):
                    toc_map.append({"title": text, "level": 2, "page_index": page_index})

    # Sort TOC to guarantee page order
    toc_map = sorted(toc_map, key=lambda x: x['page_index'])
    return toc_map

# =========================================
# PDF NATIVE PROCESSING HELPERS
# =========================================

def detect_strict_scene_break(text: str, allow_breaks_flag: bool) -> bool:
    """
    Strictly determines if a text block is a scene break for PDFs.
    Requires allow_breaks_flag (True if an image was found on page 1).
    Reuses EPUB's strict symbol rules to prevent false positives.
    """
    if not allow_breaks_flag:
        return False
        
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
        
    length = len(chars)
    if length > 20:
        return False
        
    # 1. Ban if it contains ANY letters or numbers (English, European, Asian)
    if re.search(r'[a-zA-Z0-9\u00C0-\u00FF\u0400-\u04FF\u3041-\u3096\u30A1-\u30FA\u4E00-\u9FAF\uAC00-\uD7AF]', text):
        return False
        
    # 2. Ban common punctuation, quotes, ellipses, and ALL DOTS!
    # Protects "...", "・・・", and mixed text like "***" or "..."
    forbidden_punctuation = set(".,!?:;\"'“”‘’「」『』()[]{}<>。、・？！…")
    if any(c in forbidden_punctuation for c in chars):
        return False
        
    # 3. If it has 2+ characters and survived the bans above, it is a true scene break
    if length >= 2:
        return True
        
    # 4. If it's a single character, it MUST be a verified novel separator symbol
    elif length == 1:
        valid_singles = set("*#-_~♦◇◆○●■□▼▽★☆❖✦⁂※—–―─")
        if chars[0] in valid_singles:
            return True
            
    return False


def split_pdf_sentences(text: str, start_idx: int) -> Tuple[str, int]:
    import html # Safe localized import
    
    text = text.strip()
    if not text:
        return "", start_idx
        
    pattern = r'(?<=[.!?])\s+(?=[A-Z"\'\u201c\u2018])|(?<=[。！？])\s*(?=[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])'
    chunks = re.split(pattern, text)
    
    new_html = ""
    current_idx = start_idx
    
    for c in chunks:
        chunk_text = c.strip()
        if chunk_text:
            # 🌟 FIX: Sanitize the text to prevent PDF code blocks from destroying the UI DOM
            safe_text = html.escape(chunk_text)
            new_html += f'<n id="s_{current_idx}">{safe_text}</n> '
            current_idx += 1
            
    return new_html.strip(), current_idx