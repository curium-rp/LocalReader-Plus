import re
from bs4 import BeautifulSoup

def pre_parse_clean(html_string: str) -> str:
    """
    PHASE 0: The Pre-Burner.
    Runs BEFORE BeautifulSoup even parses the HTML.
    Vaporizes XML declarations and DOCTYPEs that can confuse the parser.
    """
    html_string = re.sub(r'<\?xml.*?\?>', '', html_string, flags=re.IGNORECASE | re.DOTALL)
    html_string = re.sub(r'<!DOCTYPE.*?>', '', html_string, flags=re.IGNORECASE | re.DOTALL)
    return html_string


def normalize_epub_html(soup: BeautifulSoup, known_toc_titles: set = None) -> None:
    """
    Master pre-processing pipeline for EPUB HTML.
    Includes the 3-Branch System Manager to protect Good EPUBs.
    """
    exterminate_bad_tags(soup)
    if nuke_inline_toc(soup): return
    fix_span_fragmentation(soup)
    
    # ==========================================
    # 🌟 NEW: THE SYSTEM MANAGER (ROUTER) 🌟
    # ==========================================
    # Check if the file already has valid heading tags (Length > 2 ignores empty <h> tags)
    existing_h = [h for h in soup.find_all(['h1', 'h2', 'h3']) if len(h.get_text(strip=True)) > 2]
    has_valid_toc = known_toc_titles and len(known_toc_titles) > 2
    
    if existing_h:
        # 🟢 BRANCH 1: CLEAR CASE
        # The EPUB is good. We bypass all injection logic to protect the original H1/H2 structure.
        pass 
    elif has_valid_toc:
        # 🟡 BRANCH 2: SEMI-AUTO TOC INJECTION
        inject_headings_from_toc(soup, known_toc_titles)
    else:
        # 🔴 BRANCH 3: SUPER FALLBACK (WILD WEST)
        apply_super_fallback_headings(soup)
        
    # Deep Cleaning
    strip_junk_attributes(soup)
    heavy_paragraph_cleanup(soup)


def exterminate_bad_tags(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(['script', 'style', 'meta', 'iframe', 'link', 'noscript']):
        tag.decompose()


def nuke_inline_toc(soup: BeautifulSoup) -> bool:
    """The TOC Sniper: Vaporizes pages that are just native Table of Contents links."""
    links = soup.find_all('a')
    if links:
        link_text_len = sum(len(a.get_text(strip=True)) for a in links)
        text_content = soup.get_text(strip=True)
        text_lower = text_content.lower()
        
        is_toc_page = "table of contents" in text_lower or "contents" in text_lower or "toc" in text_lower.split()
        
        # If it claims to be a TOC, > 40% of text is links, and has > 3 links... Vaporize it.
        if is_toc_page and len(text_content) > 0 and (link_text_len / len(text_content)) > 0.4 and len(links) > 3:
            if soup.body:
                soup.body.clear()
            else:
                soup.clear()
            return True
    return False


def inject_headings_from_toc(soup: BeautifulSoup, known_toc_titles: set) -> None:
    """
    🟡 BRANCH 2 Logic: Only runs if there are NO <h> tags, but TOC exists.
    First match in the file becomes <h1>, subsequent matches become <h2>.
    """
    match_count = 0
    for block in soup.find_all(['p', 'div']):
        try:
            raw_text = block.get_text(separator=" ", strip=True)
            raw_text = " ".join(raw_text.split())
            if not raw_text or len(raw_text) > 120: continue
            
            if raw_text.lower() in known_toc_titles:
                match_count += 1
                # The first match is the main chapter title (H1). The rest are subtitles (H2).
                if match_count == 1:
                    block.name = 'h1'
                else:
                    block.name = 'h2'
        except Exception:
            pass


def apply_super_fallback_headings(soup: BeautifulSoup) -> None:
    """
    🔴 BRANCH 3 Logic: Only runs if the file is completely broken (No <h> tags, No TOC).
    Unleashes the aggressive Regex and CSS Heuristics.
    """
    heading_pattern = re.compile(
        r'^(chapter\s*[\dIVXLCDM]+|prologue|epilogue|part\s*[\dIVXLCDM]+|volume\s*[\dIVXLCDM]+)(?:[\s:,\-].*)?$', 
        re.IGNORECASE
    )
    h1_keywords = re.compile(r'^(prologue|epilogue|part\b|volume\b|book\b)', re.IGNORECASE)
    
    for block in soup.find_all(['p', 'div']):
        try:
            raw_text = block.get_text(separator=" ", strip=True)
            raw_text = " ".join(raw_text.split())
            if not raw_text or len(raw_text) > 120: continue
                
            heading_level = None
            text_lower = raw_text.lower()
            
            if heading_pattern.match(raw_text):
                heading_level = 'h1' if h1_keywords.search(text_lower) else 'h2'
            else:
                attrs = block.get('id', '').lower() + ' ' + ' '.join(block.get('class', [])).lower()
                if 'toc' in attrs or 'chapter' in attrs or 'title' in attrs:
                    if len(raw_text) < 60:
                        heading_level = 'h1' if 'title' in attrs else 'h2'
                        
                # CSS Font Size Fallback
                if not heading_level:
                    for span in block.find_all(['span', 'font']):
                        style = span.get('style', '').lower()
                        if 'bold' in style or '700' in style:
                            match = re.search(r'font-size:\s*([\d.]+)em', style)
                            if match:
                                try:
                                    size = float(match.group(1))
                                    if size >= 1.5: heading_level = 'h1'; break
                                    elif size > 1.1: heading_level = 'h2'; break
                                except Exception:
                                    pass
                                    
            if heading_level:
                block.name = heading_level
                
        except Exception:
            pass


def fix_span_fragmentation(soup: BeautifulSoup) -> None:
    for span in soup.find_all(['span', 'font']):
        span.unwrap()
    if hasattr(soup, 'smooth'):
        soup.smooth()


def strip_junk_attributes(soup: BeautifulSoup) -> None:
    target_tags = ['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 's', 'blockquote', 'li']
    junk_attrs = ['class', 'style', 'id', 'lang', 'dir', 'xml:lang']
    for tag in soup.find_all(target_tags):
        for attr in list(tag.attrs):
            if attr.lower() in junk_attrs:
                del tag.attrs[attr]


def heavy_paragraph_cleanup(soup: BeautifulSoup) -> None:
    # Global link unwrap: Clears <a> tags from headers (image cases) and normal text
    for block in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        for a_tag in block.find_all('a'):
            a_tag.unwrap()

    for block in soup.find_all(['p', 'div']):
        if block.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            continue

        raw_text = block.get_text(strip=True)
        
        # Vaporize ghost blocks
        if not raw_text and not block.find(['img', 'image', 'svg', 'picture', 'br']):
            block.decompose()
            continue

        # Whitespace normalization
        if raw_text:
            clean_string = " ".join(block.stripped_strings)
            if clean_string != raw_text and not block.find(['br', 'b', 'i', 'em', 'strong']):
                block.string = clean_string
                
        # The Fallback: Convert lazy text-heavy DIVs into P tags for uniform CSS
        if block.name == 'div' and raw_text and not block.find(['p', 'div', 'ul', 'ol', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            block.name = 'p'


def generate_toc(pages):
    toc_map = []
    junk_pattern = re.compile(r'^[\W_]+$') 
    
    for page_index, page_html in enumerate(pages):
        soup = BeautifulSoup(page_html, 'html.parser')
        body = soup.find('body') or soup
        for header in body.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            title = header.get_text(strip=True)
            if title and len(title) < 150 and not junk_pattern.match(title):
                level = int(header.name[1]) 
                if not any(t['page_index'] == page_index and t['title'] == title for t in toc_map):
                    toc_map.append({"title": title, "level": level, "page_index": page_index})

    if not toc_map:
        semantic_classes = ['chapter', 'chap', 'title', 'heading', 'h1', 'h2', 'h3']
        for page_index, page_html in enumerate(pages):
            soup = BeautifulSoup(page_html, 'html.parser')
            body = soup.find('body') or soup
            for el in body.find_all(['p', 'div', 'span'], class_=True):
                classes = el.get('class', [])
                if any(any(sc in c.lower() for sc in semantic_classes) for c in classes):
                    title = el.get_text(strip=True)
                    if title and len(title) < 150 and not junk_pattern.match(title):
                        if not any(t['page_index'] == page_index and t['title'] == title for t in toc_map):
                            toc_map.append({"title": title, "level": 1, "page_index": page_index})
                            break 

    if not toc_map:
        fallback_pattern = re.compile(r'^(chapter|prologue|epilogue|part|volume|interlude)\b|^act\s*[\dIVXLCDM]+', re.IGNORECASE)
        for page_index, page_html in enumerate(pages):
            soup = BeautifulSoup(page_html, 'html.parser')
            body = soup.find('body') or soup
            blocks_checked = 0
            for el in body.find_all(['p', 'div']):
                title = el.get_text(strip=True)
                if not title or junk_pattern.match(title):
                    continue
                blocks_checked += 1
                if len(title) < 100 and fallback_pattern.match(title):
                    if not any(t['page_index'] == page_index and t['title'] == title for t in toc_map):
                        toc_map.append({"title": title, "level": 1, "page_index": page_index})
                        break
                if blocks_checked >= 2:
                    break

    if toc_map:
        unique_levels = sorted(list(set(t['level'] for t in toc_map)))
        level_mapping = {old_lvl: new_lvl + 1 for new_lvl, old_lvl in enumerate(unique_levels)}
        for t in toc_map:
            t['level'] = level_mapping[t['level']]

    if toc_map and len(toc_map) > 2:
        duplicate_level_count = 0
        for i in range(1, len(toc_map)):
            if toc_map[i]['page_index'] == toc_map[i-1]['page_index'] and toc_map[i]['level'] == toc_map[i-1]['level']:
                duplicate_level_count += 1
        if duplicate_level_count / len(toc_map) >= 0.25:
            for i in range(1, len(toc_map)):
                if toc_map[i]['page_index'] == toc_map[i-1]['page_index'] and toc_map[i]['level'] == toc_map[i-1]['level']:
                    toc_map[i]['level'] += 1

    return sorted(toc_map, key=lambda x: x['page_index'])