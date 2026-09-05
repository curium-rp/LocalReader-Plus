"""
Content Detection & Processing Module for LocalReader plus.
"""

import re
from typing import Tuple



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