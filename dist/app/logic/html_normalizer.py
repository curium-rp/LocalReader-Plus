import re
from bs4 import BeautifulSoup

def standardize_footnotes(soup: BeautifulSoup) -> None:
    import re
    
    # 1. Lift or derive target IDs from inner anchors (LibreOffice / WordPress / Pandoc)
    for a in soup.find_all('a'):
        a_id = a.get('id') or a.get('name') or ''
        href = a.get('href', '')
        
        # Explicit ID on anchor
        if any(kw in a_id.lower() for kw in ['sdfootnote', 'footnote', 'fn-def', 'ftn', 'fnref']):
            parent = a.find_parent(['p', 'div', 'li', 'aside', 'section'])
            if parent and not parent.get('id'):
                parent['id'] = a_id
                
        # Derive ID from backlink href if anchor lacks ID
        elif href.startswith('#'):
            target = href.lstrip('#')
            derived_id = ""
            if 'anc' in target:
                derived_id = re.sub(r'anc', 'sym', target, flags=re.IGNORECASE)
            elif 'ref' in target:
                derived_id = re.sub(r'ref', 'def', target, flags=re.IGNORECASE)
                
            if derived_id:
                parent = a.find_parent(['p', 'div', 'li', 'aside', 'section'])
                if parent and not parent.get('id'):
                    parent['id'] = derived_id

    # 2. Mark definition blocks
    for block in soup.find_all(['aside', 'div', 'p', 'li', 'section']):
        b_id = block.get('id', '').lower()
        epub_type = block.get('epub:type', '').lower()
        classes = " ".join(block.get('class', [])).lower()
        
        is_def = 'footnote' in epub_type and 'noteref' not in epub_type
        if not is_def:
            if 'footnote' in classes or 'fn-def' in b_id or 'sdfootnote' in b_id or 'ftn' in b_id:
                is_def = True
                
        if is_def:
            block['epub:type'] = 'footnote'

    # 3. Contextually map links
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').lower()
        text = a.get_text(strip=True)
        epub_type = a.get('epub:type', '').lower()
        classes = " ".join(a.get('class', [])).lower()
        
        in_footnote = a.find_parent(attrs={'epub:type': 'footnote'}) is not None
        is_backlink_href = 'anc' in href or 'ref' in href or 'return' in href
        
        is_end = 'backlink' in epub_type or in_footnote
        if not is_end and (is_backlink_href or '↩' in text or '↑' in text or 'return' in text.lower()):
            is_end = True
                
        if is_end:
            a['epub:type'] = 'backlink'
            continue
            
        is_start = 'noteref' in epub_type or 'footnote-ref' in classes
        if not is_start and not in_footnote:
            if a.find_parent('sup') or a.find('sup'):
                if re.search(r'\d+', text): is_start = True
            elif re.match(r'^\[?\*?\d+\]?$', text) and '#' in href:
                is_start = True
                
        if is_start:
            a['epub:type'] = 'noteref'
            
    # 4. Fallback pass for stealth footnote blocks containing backlinks
    for a in soup.find_all('a', attrs={'epub:type': 'backlink'}):
        parent_block = a.find_parent(['aside', 'div', 'p', 'li', 'section'])
        if parent_block and parent_block.get('epub:type') != 'footnote':
            if not parent_block.get('id'):
                parent_block['id'] = f"fn_auto_{id(parent_block)}"
            parent_block['epub:type'] = 'footnote'

def pre_parse_clean(html_string: str) -> str:
    """
    PHASE 0: The Pre-Burner.
    Runs BEFORE BeautifulSoup even parses the HTML.
    Vaporizes XML declarations and DOCTYPEs that can confuse the parser.
    """
    html_string = re.sub(r'<\?xml.*?\?>', '', html_string, flags=re.IGNORECASE | re.DOTALL)
    html_string = re.sub(r'<!DOCTYPE.*?>', '', html_string, flags=re.IGNORECASE | re.DOTALL)
    return html_string





def standardize_formatting(soup: BeautifulSoup) -> None:
    # 1. Normalize semantic tags
    for tag in soup.find_all('strong'):
        tag.name = 'b'
    for tag in soup.find_all('em'):
        tag.name = 'i'
    for tag in soup.find_all(['strike', 's']):
        tag.name = 'del'

    # 2. Force any existing native tags to stay clean
    for tag in soup.find_all('u'):
        tag.name = 'u'
    for tag in soup.find_all('del'):
        tag.name = 'del'

    bold_regex = re.compile(
        r'\b(bold|bld|strong|calibre_bold|fw-bold|font-bold|b-text)\b', re.IGNORECASE
    )
    ital_regex = re.compile(
        r'\b(italic|it|em|emphasis|oblique|calibre_italic|fs-italic|i-text)\b', re.IGNORECASE
    )
    und_regex = re.compile(
        r'\b(underline|u-text|calibre_under|text-decoration-underline)\b', re.IGNORECASE
    )
    del_regex = re.compile(
        r'\b(strike|strikethrough|line-through|del|text-decoration-line-through)\b', re.IGNORECASE
    )

    # 3. Convert style / class based formatting into real tags
    for tag in list(soup.find_all(['span', 'font', 'div', 'p', 'a', 'em', 'strong'])):
        style = (tag.get('style') or '').lower().replace(' ', '')
        class_str = " ".join(tag.get('class') or []).lower()

        has_bold = (
            'font-weight:bold' in style
            or 'font-weight:700' in style
            or 'font-weight:800' in style
            or 'font-weight:900' in style
            or 'font-weight:bolder' in style
            or bold_regex.search(class_str)
        )
        has_ital = (
            'font-style:italic' in style
            or 'font-style:oblique' in style
            or ital_regex.search(class_str)
        )
        has_und = (
            'text-decoration:underline' in style
            or 'text-decoration-line:underline' in style
            or und_regex.search(class_str)
        )
        has_del = (
            'text-decoration:line-through' in style
            or 'text-decoration-line:line-through' in style
            or del_regex.search(class_str)
        )

        if not (has_bold or has_ital or has_und or has_del):
            continue

        # Build nested real tags
        content = list(tag.contents)

        def wrap(contents, tag_name):
            w = soup.new_tag(tag_name)
            for c in contents:
                w.append(c)
            return w

        new_content = content
        if has_del:
            new_content = [wrap(new_content, 'del')]
        if has_und:
            new_content = [wrap(new_content, 'u')]
        if has_ital:
            new_content = [wrap(new_content, 'i')]
        if has_bold:
            new_content = [wrap(new_content, 'b')]

        tag.clear()
        for item in new_content:
            tag.append(item)

    # 4. Final safety: make sure every u/del that exists is a real tag
    for tag in soup.find_all(True):
        style = (tag.get('style') or '').lower().replace(' ', '')
        if 'text-decoration:underline' in style or 'text-decoration-line:underline' in style:
            if tag.name not in ('u', 'b', 'i', 'del'):
                tag.name = 'u'
        if 'text-decoration:line-through' in style or 'text-decoration-line:line-through' in style:
            if tag.name not in ('del', 'b', 'i', 'u'):
                tag.name = 'del'

def normalize_epub_html(soup: BeautifulSoup, known_toc_titles: set = None, current_href: str = None, rich_toc_map: dict = None) -> None:
    """
    Master pre-processing pipeline for EPUB HTML.
    Includes the 3-Branch System Manager to protect Good EPUBs.
    """
    exterminate_bad_tags(soup)
    if nuke_inline_toc(soup): return
    promote_image_headers(soup)
    standardize_formatting(soup)
    fix_span_fragmentation(soup)
    
    standardize_footnotes(soup)
    
    # ==========================================
    # 🌟 NEW: THE SYSTEM MANAGER (ROUTER) 🌟
    # ==========================================
    # Check if the file already has valid heading tags (Length > 2 ignores empty <h> tags)
    existing_h = [h for h in soup.find_all(['h1', 'h2', 'h3']) if len(h.get_text(strip=True)) > 2]
    has_valid_toc = known_toc_titles and len(known_toc_titles) > 2
    
    if existing_h:
        # 🌟 BRANCH 1: CLEAR CASE
        # The EPUB is good. We bypass all injection logic to protect the original H1/H2 structure.
        pass 
    else:
        # 検 BRANCH 3: SUPER FALLBACK (WILD WEST)
        apply_super_fallback_headings(soup)
        
    if not inject_mapped_image_headings(soup, current_href, rich_toc_map):
        inject_image_headings(soup, known_toc_titles)
        
    # Deep Cleaning
    strip_junk_attributes(soup)
    heavy_paragraph_cleanup(soup)


def inject_mapped_image_headings(soup: BeautifulSoup, current_href: str, rich_toc_map: dict) -> bool:
    if not current_href or not rich_toc_map: return False
    if soup.find(['h1', 'h2']): return False
    
    clean_href = current_href.split('/')[-1].split('#')[0].lower()
    expected_nodes = rich_toc_map.get(clean_href, [])
    if not expected_nodes: return False
    
    for img in soup.find_all(['img', 'image']):
        parent = img.find_parent(['div', 'p', 'section'])
        parent_id = (parent.get('id') or '').lower() if parent else ''
        img_alt = (img.get('alt') or '').lower()
        a_tag = img.find_parent('a')
        a_href = (a_tag.get('href') or '').lower() if a_tag else ''
        
        for expected in expected_nodes:
            match = False
            anchor = expected.get('anchor', '')
            title = expected.get('clean_title', '')
            
            if anchor and (anchor in parent_id or anchor in a_href): match = True
            elif title and len(title) > 3 and title in img_alt: match = True
            
            if match:
                wrap = img.find_parent(['div', 'p'])
                if wrap and not wrap.get_text(strip=True):
                    wrap.name = 'h1'
                    
                    if len(img_alt) < 4 or 'image' in img_alt or 'img' in img_alt:
                        span = soup.new_tag('span')
                        span['class'] = 'epub-visually-hidden'
                        span.string = expected.get('title', '')
                        wrap.insert(0, span)
                    return True
    return False


def inject_image_headings(soup: BeautifulSoup, known_toc_titles: set) -> None:
    if soup.find(['h1', 'h2']): return
    
    for img in soup.find_all(['img', 'image']):
        src = (img.get('src') or '').lower()
        alt = (img.get('alt') or '').lower()
        
        # 🌟 Anti-false-positive shield
        if any(x in alt for x in ['cover', 'title page', 'illustration', 'insert', 'frontispiece', 'copyright']):
            continue
            
        filename = src.split('/')[-1].split('.')[0]
        match_found = False
        
        if known_toc_titles:
            for title in known_toc_titles:
                if len(title) < 4: continue # Too short, prone to false positive
                
                if title == alt or title in alt:
                    match_found = True
                    break
                    
                nums = re.findall(r'\d+', title)
                if nums:
                    for num in nums:
                        if num in filename or num.zfill(2) in filename or num.zfill(3) in filename:
                            if any(kw in filename for kw in ['ch', 'chap', 'chapter', 'part', 'vol']):
                                match_found = True
                                break
                if match_found: break
                
                clean_title = "".join(c for c in title if c.isalnum())
                clean_file = "".join(c for c in filename if c.isalnum())
                clean_alt = "".join(c for c in alt if c.isalnum())
                
                if clean_title and (clean_title in clean_file or clean_title in clean_alt):
                    match_found = True
                    break
                    
        if not match_found:
            strict_pattern = re.compile(r'^(chapter|prologue|epilogue|part|volume)[\s_\-]*[\dIVX]+$', re.IGNORECASE)
            if strict_pattern.match(filename) or strict_pattern.match(alt):
                match_found = True
                
        if match_found:
            wrap = img.find_parent(['div', 'p'])
            if wrap and not wrap.get_text(strip=True):
                wrap.name = 'h1'
                break

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


def promote_image_headers(soup: BeautifulSoup):
    for h in soup.find_all(['h1', 'h2', 'h3']):
        if not h.get_text(strip=True) and not h.find(['img', 'svg']):
            next_node = h.find_next_sibling()
            if next_node and next_node.name in ['div', 'p', 'section']:
                img_wrap = next_node.find('a')
                img = next_node.find('img')
                if img:
                    if img_wrap and img in img_wrap.descendants:
                        h.append(img_wrap.extract())
                    else:
                        h.append(img.extract())
                    if not next_node.get_text(strip=True):
                        next_node.decompose()


def fix_span_fragmentation(soup: BeautifulSoup) -> None:
    reserved_classes = {'epub-footnote', 'epub-noteref', 'pagebreak', 'page-break', 'epub-visually-hidden'}
    # list() prevents skipping elements when unwrap() modifies DOM
    for span in list(soup.find_all(['span', 'font', 'label', 'small', 'big', 'abbr', 'dfn', 'kbd', 'samp', 'var', 'mark', 'ruby', 'rt', 'rp', 'bdi', 'bdo', 'time', 'data', 'tt', 'cite', 'q'])):
        classes = span.get('class', [])
        if isinstance(classes, str): 
            classes = [classes]
            
        if any(c.lower() in reserved_classes for c in classes):
            continue
            
        span_id = span.get('id')
        if span_id:
            parent = span.find_parent(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            if parent and not parent.get('id'):
                parent['id'] = span_id
                
        span.unwrap()
        
    if hasattr(soup, 'smooth'):
        soup.smooth()


def strip_junk_attributes(soup: BeautifulSoup) -> None:
    junk_attrs = ['class', 'style', 'lang', 'dir', 'xml:lang', 'align', 'valign', 'bgcolor', 'color', 'role', 'type', 'epub:type']
    for tag in soup.find_all(True):
        classes = tag.get('class', [])
        if isinstance(classes, str): 
            classes = [classes]
            
        if classes:
            tag['data-orig-class'] = " ".join(classes)
            
        is_protected = any(c in classes for c in ['epub-footnote', 'epub-noteref', 'epub-image', 'epub-visually-hidden'])
        
        for attr in list(tag.attrs):
            if attr.lower() in junk_attrs:
                if is_protected and attr.lower() == 'class':
                    tag['class'] = [c for c in classes if c in ['epub-footnote', 'epub-noteref', 'epub-image', 'epub-visually-hidden']]
                    continue
                # 🌟 FIX: Protect footnote identification attributes from being vaporized!
                if attr.lower() == 'epub:type':
                    val = tag.get('epub:type', '')
                    if isinstance(val, list): val = " ".join(val)
                    if any(t in val.lower() for t in ['noteref', 'footnote', 'backlink']):
                        continue
                del tag.attrs[attr]

def heavy_paragraph_cleanup(soup: BeautifulSoup) -> None:
    # Global link unwrap: Vaporize all <a> tags globally. Salvage IDs.
    for a_tag in list(soup.find_all('a')):
        classes = a_tag.get('class', [])
        if isinstance(classes, str): 
            classes = [classes]
        epub_type = a_tag.get('epub:type', '')
            
        is_protected = 'noteref' in epub_type or 'footnote' in epub_type or 'backlink' in epub_type or 'epub-noteref' in classes or 'epub-footnote' in classes or 'epub-backlink' in classes
        has_image = a_tag.find(['img', 'svg', 'picture']) is not None
        
        if is_protected and not has_image:
            continue
            
        a_id = a_tag.get('id') or a_tag.get('name')
        if a_id:
            if not a_tag.get_text(strip=True) and not has_image:
                next_node = a_tag.find_next(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                if next_node and not next_node.get('id'):
                    next_node['id'] = a_id
                    next_node['data-orig-id'] = a_id
            else:
                child = a_tag.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div'])
                if child and not child.get('id'):
                    child['id'] = a_id
                    child['data-orig-id'] = a_id
                else:
                    parent = a_tag.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'section'])
                    if parent and not parent.get('id'):
                        parent['id'] = a_id
                        parent['data-orig-id'] = a_id
                        
        a_tag.unwrap()

    for block in soup.find_all(['p', 'div']):
        raw_text = block.get_text(strip=True)
        
        # Vaporize ghost blocks
        if not raw_text and not block.find(['img', 'image', 'svg', 'picture', 'br', 'a']):
            block.decompose()
            continue

        # Whitespace normalization
        if raw_text and not block.find(['p', 'div', 'ul', 'ol', 'table', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            clean_string = " ".join(block.stripped_strings)
            # 🌟 FIX: Do not strip spaces if formatting markers are inside!
            if clean_string != raw_text and not block.find(['br', 'b', 'i', 'em', 'strong', 'a']) and '§§' not in raw_text:
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
                    toc_map.append({"title": title, "level": level, "page_index": page_index, "anchor_id": header.get('id')})

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
                            toc_map.append({"title": title, "level": 1, "page_index": page_index, "anchor_id": el.get('id')})
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
                        toc_map.append({"title": title, "level": 1, "page_index": page_index, "anchor_id": el.get('id')})
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