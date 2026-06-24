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
        generate_toc
    )
except ImportError:
    sys.path.append(str(base_dir))
    try:
        from logic.smart_content_detector import (
            detect_headers_footers,
            apply_header_footer_filter,
            generate_toc
        )
    except ImportError:
        pass

router = APIRouter()

# =========================================
# NEW STRUCTURAL PAYLOAD MODEL
# =========================================
class ProgressUpdatePayload(BaseModel):
    currentPage: int
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: int
    lastAccessed: float

# --- SURGICAL HELPER: Backward Compatibility ---
def get_doc_json_path(doc_id: str) -> Path:
    new_path = content_dir / doc_id / f"{doc_id}.json"
    if new_path.exists():
        return new_path
    old_path = content_dir / f"{doc_id}.json"
    if old_path.exists():
        return old_path
    raise HTTPException(status_code=404, detail="Document not found")

@router.post("/api/convert/epub")
async def convert_epub(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)): 
    import re
    import shutil
    import posixpath
    import urllib.parse
    from bs4 import BeautifulSoup
    import ebooklib
    from ebooklib import epub
    from fastapi import HTTPException
    from logic.smart_content_detector import clean_epub_html, generate_toc

    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Not an EPUB file")

    doc_id = id
    book_dir = content_dir / doc_id
    book_dir.mkdir(parents=True, exist_ok=True)
    temp_epub = book_dir / "temp.epub"

    def split_sentences(text):
        text = text.strip()
        if not text: return []
        
        # 🌟 CJK SPLIT FIX: 
        # Matches English punctuation followed by a space, OR CJK punctuation (。！？) without needing a space.
        pattern = r'(?<=[.!?])\s+(?=[A-Z"\'\u201c\u2018])|(?<=[。！？])\s*(?=[\u4e00-\u9fa5\u3040-\u30ff"\'\u201c\u2018])'
        chunks = re.split(pattern, text)
        return [c for c in chunks if c.strip()]

    try:
        with open(temp_epub, "wb") as f:
            content = await file.read()
            f.write(content)

        try:
            book = epub.read_epub(str(temp_epub))
        except Exception:
            shutil.rmtree(book_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Cannot read protected file (DRM)")

        pages = []
        image_map = {}
        image_counter = 1
        global_sentence_idx = 0

        spine_tuples = getattr(book, 'spine', [])
        
        for spine_item in spine_tuples:
            item_id = spine_item[0]
            item = book.get_item_with_id(item_id)
            
            if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            raw_html = item.get_content().decode('utf-8', 'ignore')
            
            cleaned_html = clean_epub_html(raw_html)
            soup = BeautifulSoup(cleaned_html, "html.parser")
            html_dir = posixpath.dirname(item.get_name())
            
            href_to_image_id = {}
            for img in soup.find_all(['img', 'image']):
                if img.parent is None:
                    continue

                src = img.get('src') or img.get('xlink:href') or img.get('href')
                
                if src:
                    src = src.split('#')[0]
                    resolved_href = urllib.parse.unquote(posixpath.normpath(posixpath.join(html_dir, src))).lstrip('/')
                    
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
                    
                    if image_item:
                        actual_item_name = image_item.get_name()
                        
                        if actual_item_name in href_to_image_id:
                            assigned_id = href_to_image_id[actual_item_name]
                        else:
                            ext = posixpath.splitext(actual_item_name)[1].lower()
                            if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']: 
                                ext = ".jpg" 
                            
                            image_filename = f"image_{image_counter}{ext}"
                            image_path = book_dir / image_filename
                            
                            with open(image_path, "wb") as img_file:
                                img_file.write(image_item.get_content())
                            
                            image_map[str(image_counter)] = image_filename
                            href_to_image_id[actual_item_name] = str(image_counter)
                            assigned_id = str(image_counter)
                            image_counter += 1

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
                        svg_wrapper = img.find_parent('svg')
                        if svg_wrapper:
                            svg_wrapper.decompose()
                        else:
                            img.decompose()
                else:
                    svg_wrapper = img.find_parent('svg')
                    if svg_wrapper:
                        svg_wrapper.decompose()
                    else:
                        img.decompose()

            for p in soup.find_all(['p', 'div']):
                if not p.find('img'):
                    p_text = p.get_text(strip=True)
                    chars = [c for c in p_text if not c.isspace()]
                    
                    if not chars:
                        continue
                        
                    length = len(chars)
                    if length > 20:
                        continue
                        
                    # 1. Ban if it contains ANY letters or numbers (English, European, Asian)
                    if re.search(r'[a-zA-Z0-9\u00C0-\u00FF\u0400-\u04FF\u3041-\u3096\u30A1-\u30FA\u4E00-\u9FAF\uAC00-\uD7AF]', p_text):
                        continue
                        
                    # 2. Ban common punctuation, quotes, ellipses, and ALL DOTS! 
                    # This explicitly protects "...", "・・・", and "。。" from being marked as scene breaks.
                    forbidden_punctuation = set(".,!?:;\"'“”‘’「」『』()[]{}<>。、・？！…")
                    if any(c in forbidden_punctuation for c in chars):
                        continue
                        
                    is_scene_break = False
                    
                    # 3. If it has 2+ characters and survived the bans above, it is a true scene break (e.g., ***, ###, ◇◇◇, ――)
                    if length >= 2:
                        is_scene_break = True
                        
                    # 4. If it's a single character, it MUST be a verified novel separator symbol
                    elif length == 1:
                        valid_singles = set("*#-_~♦◇◆○●■□▼▽★☆❖✦⁂※—–―─")
                        if chars[0] in valid_singles:
                            is_scene_break = True
                            
                    # Inject the scene break <s> tag for the TTS engine
                    if is_scene_break:
                        sb = soup.new_tag('s')
                        sb.string = p_text
                        p.replace_with(sb)

            for element in soup.find_all(string=True):
                if not element.parent or element.parent.name in ['script', 'style', 'head', 'title', 'meta', '[document]', 's']:
                    continue
                
                text = str(element).strip()
                if not text: continue
                    
                sentences = split_sentences(text)
                if sentences:
                    new_html = ""
                    for s in sentences:
                        new_html += f'<n id="s_{global_sentence_idx}">{s}</n> '
                        global_sentence_idx += 1
                    
                    wrapper = BeautifulSoup(new_html, "html.parser")
                    safe_container = soup.new_tag("span")
                    for child in list(wrapper.contents):
                        safe_container.append(child)
                        
                    element.replace_with(safe_container)
                    safe_container.unwrap()

            for block in soup.find_all(['div', 'p', 'figure']):
                if not block.get_text(strip=True) and not block.find(['img', 'hr', 'br', 'svg', 'picture']):
                    block.decompose()

            body = soup.find('body')
            page_html = str(body) if body else str(soup)
            
            if "<n id=" in page_html or "<img" in page_html or "<s>" in page_html:
                pages.append(page_html)

        temp_epub.unlink(missing_ok=True)
        toc_map = generate_toc(pages)

        return {
            "pages": pages,
            "image_map": image_map,
            "toc_map": toc_map
        }

    except Exception as e:
        shutil.rmtree(book_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/convert/pdf")
async def convert_pdf(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    import shutil
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise HTTPException(status_code=500, detail="PyMuPDF library not installed. Run 'pip install PyMuPDF'")
        
    from fastapi import HTTPException
    from logic.smart_content_detector import detect_strict_scene_break, split_pdf_sentences, generate_toc

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
        
        # ONLY true full-stops complete a <p> tag. Commas and semicolons trigger cross-page holding.
        paragraph_terminators = (".", "!", "?", "…", "。", "！", "？", "”", '"', "’", "'", "」", "』")

        for page_index in range(len(doc)):
            page = doc[page_index]
            page_html = ""
            elements = []
            
            # 1. Extract Tables (No over-engineering, just pull the grid)
            table_bboxes = []
            if hasattr(page, "find_tables"):
                for tab in page.find_tables():
                    elements.append({
                        "type": "table",
                        "bbox": tab.bbox,
                        "data": tab.extract()
                    })
                    table_bboxes.append(tab.bbox)

            # 2. Extract Text and Images
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                b_bbox = block["bbox"]
                
                # Safety: If this text block is inside a table we already extracted, skip it to prevent duplicates
                is_in_table = False
                for t_bbox in table_bboxes:
                    cx = (b_bbox[0] + b_bbox[2]) / 2
                    cy = (b_bbox[1] + b_bbox[3]) / 2
                    if t_bbox[0] <= cx <= t_bbox[2] and t_bbox[1] <= cy <= t_bbox[3]:
                        is_in_table = True
                        break
                        
                if not is_in_table:
                    elements.append({
                        "type": "text" if block["type"] == 0 else "image",
                        "bbox": b_bbox,
                        "block": block
                    })
            
            # 3. Sort everything strictly top-to-bottom so the flow is flawless
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
                    if block_text.startswith('•'):
                        block_text = block_text[1:].strip()
                        
                    if not block_text or block_text in ['•', '-', '·']:
                        continue

                    # Pre-check types to see if we need to forcefully flush the held text
                    is_header = False
                    if max_fontsize > 14 and len(block_text) < 100 and not block_text.endswith(paragraph_terminators):
                        is_header = True
                        
                    is_scene_break = detect_strict_scene_break(block_text, allow_scene_breaks)

                    # If the new block is a structural break (header/scene), the previous paragraph MUST be finished
                    if (is_header or is_scene_break) and held_text:
                        sentences_html, global_sentence_idx = split_pdf_sentences(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>\n"
                        held_text = ""

                    # 🌟 THE STITCHING FIX: If no structural break, fuse with previous held text
                    if not is_header and not is_scene_break and held_text:
                        if held_text.endswith("-") and not held_text.endswith(" -"):
                            block_text = held_text[:-1] + block_text
                        else:
                            block_text = held_text + " " + block_text
                        held_text = ""

                    # Determine if this combined block STILL needs to be held (e.g. crossing to next page)
                    if not is_header and not is_scene_break and not block_text.endswith(paragraph_terminators):
                        held_text = block_text
                        continue

                    # Final Output Generation
                    if is_scene_break:
                        page_html += f"<s>{block_text}</s>\n"
                    elif is_header:
                        sentences_html, global_sentence_idx = split_pdf_sentences(block_text, global_sentence_idx)
                        page_html += f"<h2>{sentences_html}</h2>\n"
                    else:
                        sentences_html, global_sentence_idx = split_pdf_sentences(block_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>\n"

                elif element["type"] == "image":
                    # Force flush held text before rendering an image
                    if held_text:
                        sentences_html, global_sentence_idx = split_pdf_sentences(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>\n"
                        held_text = ""
                        
                    block = element["block"]
                    try:
                        width = block.get("width", 0)
                        height = block.get("height", 0)
                        if width < 50 or height < 50:
                            continue
                            
                        image_bytes = block.get("image")
                        image_ext = block.get("ext", "jpg")
                        
                        if not image_bytes or len(image_bytes) < 1024:
                            continue
                            
                        image_filename = f"image_{image_counter}.{image_ext}"
                        image_path = book_dir / image_filename
                        
                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)
                            
                        image_map[str(image_counter)] = image_filename
                        assigned_id = str(image_counter)
                        image_counter += 1
                        
                        page_html += f'<img src="/api/library/image/{doc_id}/{assigned_id}" class="epub-image" loading="lazy" style="max-width:100%; height:auto;" />\n'
                    except Exception:
                        pass 

                elif element["type"] == "table":
                    # Force flush held text before rendering a table
                    if held_text:
                        sentences_html, global_sentence_idx = split_pdf_sentences(held_text, global_sentence_idx)
                        page_html += f"<p>{sentences_html}</p>\n"
                        held_text = ""
                        
                    table_html = "<table class='pdf-table' border='1' style='border-collapse: collapse; width: 100%; margin: 10px 0;'>\n"
                    for row in element["data"]:
                        table_html += "<tr>"
                        for cell in row:
                            cell_text = str(cell) if cell else ""
                            if cell_text.strip():
                                chunk, global_sentence_idx = split_pdf_sentences(cell_text.strip(), global_sentence_idx)
                                table_html += f"<td style='padding: 6px;'>{chunk}</td>"
                            else:
                                table_html += "<td></td>"
                        table_html += "</tr>\n"
                    table_html += "</table>\n"
                    page_html += table_html

            if page_html.strip():
                pages.append(f'<div class="pdf-page">\n{page_html}</div>')
            else:
                # 🌟 FIX: Wrap blank pages in the structural <n> tag to prevent frontend legacy fallback
                pages.append(f'<div class="pdf-page">\n<p><n id="s_{global_sentence_idx}">[Blank Page]</n></p>\n</div>')
                global_sentence_idx += 1

        # Clean up any dangling text at the very end of the document
        if held_text:
            sentences_html, global_sentence_idx = split_pdf_sentences(held_text, global_sentence_idx)
            if pages:
                pages[-1] = pages[-1].replace('</div>', f'<p>{sentences_html}</p>\n</div>')
            else:
                pages.append(f'<div class="pdf-page">\n<p>{sentences_html}</p>\n</div>')

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
    # 🌟 SURGICAL FIX: Force UTF-8 decoding to prevent Windows crash
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

# =========================================
# THE NEW DUAL-SAVE CHECKPOINT ROUTE
# =========================================
@router.post("/api/library/progress/{doc_id}")
async def update_book_progress_checkpoint(doc_id: str, payload: ProgressUpdatePayload):
    """
    Surgically dual-saves the reading state.
    Updates the global library.json AND the specific book's internal JSON.
    """
    if not library_file.exists():
        raise HTTPException(status_code=404, detail="Library inventory log absent.")

    # 1. Update Global Library (library.json)
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
        raise HTTPException(status_code=500, detail=f"Global database sync failure: {str(io_error)}")

    # 2. Update Specific Book Content JSON (Dual-Save)
    try:
        book_file_path = get_doc_json_path(doc_id)
        if book_file_path.exists():
            with open(book_file_path, "r", encoding="utf-8") as f:
                book_data = json.load(f)
            
            # Inject progress metadata directly into the book file
            book_data["currentPage"] = payload.currentPage
            book_data["lastSentenceId"] = payload.lastSentenceId
            book_data["lastSentenceIndex"] = payload.lastSentenceIndex
            book_data["lastAccessed"] = payload.lastAccessed

            temp_book_path = book_file_path.with_suffix(".tmp")
            with open(temp_book_path, "w", encoding="utf-8") as write_handle:
                json.dump(book_data, write_handle, indent=4, ensure_ascii=False)
            temp_book_path.replace(book_file_path)
            
    except Exception as e:
        print(f"[Warning] Failed to dual-save individual book {doc_id}.json: {str(e)}")

    return {"status": "success", "message": f"Checkpoint dual-saved for {doc_id}"}