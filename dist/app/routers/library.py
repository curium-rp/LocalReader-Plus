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
    from logic.html_normalizer import generate_toc
except ImportError:
    sys.path.append(str(base_dir))
    try:
        from logic.smart_content_detector import (
            detect_headers_footers,
            apply_header_footer_filter,
            detect_strict_scene_break
        )
        from logic.html_normalizer import generate_toc
    except ImportError:
        pass

router = APIRouter()

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

# =========================================
# 🌟 NEW: THE MASTER SENTENCE SPLITTER 🌟
# =========================================
def master_sentence_splitter(text: str, start_idx: int = 0):
    text = text.strip()
    if not text: 
        return "", start_idx
        
    import re
    text = re.sub(r'\.\s+\.\s+\.', '...', text)
    
    abbreviations = [
        "Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Rd", "Ave", "Capt",
        "Gen", "Sen", "Rep", "Gov", "Fig", "No", "Op", "vs", "etc",
        "Inc", "Ltd", "Co"
    ]
    for abbr in abbreviations:
        text = re.sub(rf'\b({abbr})\.(?=\s)', r'\1<ABBR>', text, flags=re.IGNORECASE)
        
    text = re.sub(r'(?i)\b(e\.g)\.(?=\s)', r'\1<ABBR>', text)
    text = re.sub(r'(?i)\b(i\.e)\.(?=\s)', r'\1<ABBR>', text)
    
    # 🌟 FIX 1: FULL STOP ONLY 
    # Removed ! and ? so fast dialogue and questions stay glued together!
    pattern = (
        r'(?<=[.])\s+(?=[A-Z"\'\u201c\u2018])|'
        r'(?<=[.][\'"”’])\s+(?=[A-Z"\'\u201c\u2018])|'
        r'(?<=[。])\s*(?=[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])|'
        r'(?<=[。][\'"”’])\s*(?=[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])'
    )
    # 🌟 FIX: Clean chunks early so we can index them accurately
    raw_chunks = re.split(pattern, text)
    chunks = [c.strip() for c in raw_chunks if c.strip()]
    
    html_out = ""
    current_idx = start_idx
    buffer = ""
    
    for i, c in enumerate(chunks):
        if buffer:
            buffer += " " + c
        else:
            buffer = c
            
        # Count approximate words in the current buffer
        word_count = len(re.findall(r'\b\w+\b', buffer))
        
        # 🌟 FIX: Bulletproof array index check
        # Wait if it's too short AND not the last chunk in the array
        if word_count < 4 and i != len(chunks) - 1:
            continue
            
        clean_chunk = buffer.replace('<ABBR>', '.')
        html_out += f'<n id="s_{current_idx}">{clean_chunk}</n> '
        current_idx += 1
        buffer = ""
            
    return html_out.strip(), current_idx


@router.post("/api/convert/epub")
async def convert_epub(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)): 
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
        
        try:
            # BRANCH 1: Native EPUB Metadata TOC (Bulletproofed & Sanitized)
            if hasattr(book, 'toc'):
                def extract_early_titles(items):
                    if not isinstance(items, (list, tuple)): return
                    for item in items:
                        try:
                            if isinstance(item, (tuple, list)) and len(item) == 2:
                                if hasattr(item[0], 'title') and item[0].title:
                                    clean_title = " ".join(str(item[0].title).split()).lower()
                                    known_toc_titles.add(clean_title)
                                extract_early_titles(item[1])
                            elif hasattr(item, 'title') and item.title:
                                clean_title = " ".join(str(item.title).split()).lower()
                                known_toc_titles.add(clean_title)
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
                from logic.html_normalizer import pre_parse_clean, normalize_epub_html
                raw_html = pre_parse_clean(raw_html)
            except Exception:
                pass
            
            soup = BeautifulSoup(raw_html, "html.parser")
            
            # 🌟 2. Execute the Master Pipeline (handles tags, headings, cleanup)
            try:
                # pass known_toc_titles generated earlier in convert_epub
                normalize_epub_html(soup, known_toc_titles=known_toc_titles) 
            except Exception as e:
                print(f"[Warning] HTML Normalizer failed: {e}")
                
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
                    
                    import re
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
                        valid_singles = set("*#-_~♦◇◆○●■□▼▽★☆❖✦⁂※—–―─")
                        if chars[0] in valid_singles:
                            is_scene_break = True
                            
                    if is_scene_break:
                        sb = soup.new_tag('s')
                        sb.string = p_text
                        p.replace_with(sb)

            for block in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote']):
                # 🌟 FIX: Protect native headings from being eaten by parent DIVs
                if block.find(['p', 'div', 'ul', 'ol', 'table', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    continue
                
                if block.find(['img', 's', 'picture', 'svg', 'figure']):
                    continue

                # 🌟 FIX: Protect <br> tags from being wiped out by get_text()
                for br in block.find_all('br'):
                    br.replace_with(" XBRX ")

                text = block.get_text(separator=" ", strip=True)
                
                # If the block was ONLY <br> tags, restore them visually and skip TTS wrapping
                if text.replace("XBRX", "").strip() == "":
                    block.clear()
                    for _ in range(text.count("XBRX")):
                        block.append(BeautifulSoup("<br/>", "html.parser"))
                    continue

                if not text:
                    continue

                safe_text = html.escape(text)

                if block.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    block.clear()
                    block['id'] = f"s_{global_sentence_idx}"
                    # Restore <br/> tags natively into the header without an <n> wrapper
                    header_html = safe_text.replace(" XBRX ", "<br/>").replace("XBRX", "<br/>")
                    block.append(BeautifulSoup(header_html, "html.parser"))
                    global_sentence_idx += 1
                    continue

                new_html, global_sentence_idx = master_sentence_splitter(safe_text, global_sentence_idx)
                
                if new_html:
                    # 🌟 FIX: Inject the <br> tags back into the HTML stream!
                    new_html = new_html.replace(" XBRX ", "<br/>").replace("XBRX", "<br/>")
                    
                    block.clear() 
                    wrapper = BeautifulSoup(new_html, "html.parser")
                    block.append(wrapper)

            for block in soup.find_all(['div', 'p', 'figure', 'span']):
                if not block.get_text(strip=True) and not block.find(['img', 'hr', 'br', 'svg', 'picture', 's', 'n']):
                    block.decompose()

            body = soup.find('body')
            page_html = str(body) if body else str(soup)
            
            # Minify: Eliminate all linebreaks and whitespace exactly between closing and opening tags
            page_html = re.sub(r'>\s*\n+\s*<', '><', page_html)
            
            if "<n id=" in page_html or "<img" in page_html or "<s>" in page_html:
                href_to_page[actual_href] = len(pages)
                pages.append(page_html)

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
                            clean_href = str(href).split('#')[0]
                            
                            idx = href_to_page.get(clean_href, -1)
                            if idx == -1:
                                for h, p in href_to_page.items():
                                    if posixpath.basename(h) == posixpath.basename(clean_href):
                                        idx = p
                                        break
                                        
                            if idx != -1:
                                # Shield against 'None' titles crashing the UI later
                                title_str = str(getattr(section, 'title') or f"Chapter (Page {idx + 1})")
                                res.append({"title": title_str, "level": level, "page_index": idx})
                                
                            res.extend(parse_native_toc(children, level + 1))
                        else:
                            res.extend(parse_native_toc(item, level))
                    elif hasattr(item, 'title') and hasattr(item, 'href'):
                        href = getattr(item, 'href', '') or ''
                        clean_href = str(href).split('#')[0]
                        
                        idx = href_to_page.get(clean_href, -1)
                        if idx == -1:
                            for h, p in href_to_page.items():
                                if posixpath.basename(h) == posixpath.basename(clean_href):
                                    idx = p
                                    break
                                    
                        if idx != -1:
                            # Shield against 'None' titles crashing the UI later
                            title_str = str(getattr(item, 'title') or f"Chapter (Page {idx + 1})")
                            res.append({"title": title_str, "level": level, "page_index": idx})
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
            try:
                toc_map = generate_toc(pages)
            except Exception as e:
                print(f"[Warning] Fallback HTML TOC gen failed: {e}")
                toc_map = [{"title": "Start of Book", "level": 1, "page_index": 0}]

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
                toc_map.append({"title": title, "level": lvl, "page_index": max(0, page_num - 1)})

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
            if pages:
                pages[-1] = pages[-1].replace('</div>', f'<p>{sentences_html}</p></div>')
            else:
                pages.append(f'<div class="pdf-page"><p>{sentences_html}</p></div>')

        doc.close()
        temp_pdf.unlink(missing_ok=True)
        
        if not toc_map:
            toc_map = generate_toc(pages)

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
        with open(library_file, "r") as f:
            return json.load(f)
    except Exception:
        return []

@router.post("/api/library")
def save_library_item(item: LibraryItem):
    try:
        with open(library_file, "r") as f:
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
def delete_library_item(doc_id: str):
    try:
        with open(library_file, "r") as f:
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