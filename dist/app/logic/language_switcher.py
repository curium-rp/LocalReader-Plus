import re
from typing import Callable, Tuple, List, Dict, Any

# 🌟 STRICT 3-LANGUAGE FALLBACK MAPPING
# Only English, Japanese, and Chinese (Mandarin) are supported for auto-switching.
FALLBACK_VOICES = {
    'en': 'af_sky',          
    'ja': 'jf_nezumi',       
    'cmn': 'zf_xiaoxiao'
}

def _determine_chunk_lang(chunk: str, base_lang: str = 'en', is_jp_context: bool = False, is_cmn_context: bool = False) -> str:
    """Robust heuristics to determine the language script of a substring block."""
    if not chunk or not chunk.strip():
        return 'unknown'
    
    # Pre-calculate character hits
    jp_kana = len(re.findall(r'[\u3041-\u3096\u30A1-\u30FA]', chunk))
    cjk_kanji = len(re.findall(r'[\u4E00-\u9FFF]', chunk))
    latin_chars = len(re.findall(r'[a-zA-Z\u00C0-\u00FF]', chunk))

    # Rule 1: Japanese (Kana strictly guarantees Japanese)
    if jp_kana > 0: 
        return 'ja'
        
    # Rule 2: Chinese vs. Japanese Kanji Resolution
    if cjk_kanji > 0: 
        if base_lang == 'ja': return 'ja'
        if base_lang in ('cmn', 'zh'): return 'cmn'
        # Contextual Kanji Resolution
        if is_jp_context: return 'ja'
        return 'cmn'
    
    # Rule 3: Latin Standard (English, Spanish, French, etc.)
    if latin_chars > 0: 
        # 🌟 THE JARRING VOICE SWITCH SHIELD (UPGRADED)
        # If the chunk is purely uppercase acronyms (ISS), single letters (X), 
        # or attached to numbers (V2), force CJK context to prevent voice stutter.
        # But if it contains lowercase letters (Commercial Crew Program), 
        # it is a real English phrase. Allow it to switch to the English voice!
        is_acronym_or_single = bool(re.fullmatch(r'[\s0-9A-Z]+', chunk.strip()))
        
        if is_acronym_or_single:
            if is_jp_context: return 'ja'
            if is_cmn_context: return 'cmn'
            
        # If it's a longer phrase, or has lowercase letters, respect the base language
        if base_lang not in ('ja', 'cmn', 'zh'):
            return base_lang
            
        return 'en'
        
    return 'unknown'

def smart_polyglot_split(text: str, current_voice: str, lang_resolver: Callable[[str], str]) -> List[Dict[str, Any]]:
    """
    Slices inline text by character group type.
    Ensures safe, file-verified voice assignment to protect runtime integrity.
    """
    if not text or not text.strip():
        return []

    current_lang_code = lang_resolver(current_voice)
    base_current_lang = current_lang_code.split('-')[0] if '-' in current_lang_code else current_lang_code
    
    # 🌟 NEW BYPASS: Deactivate auto-switcher for unsupported languages
    # If the voice is Spanish, French, Italian, Portuguese, or Hindi, bypass the polyglot logic.
    # The user maintains full control and the engine reads natively.
    if base_current_lang not in ('en', 'ja', 'cmn', 'zh'):
        return [{
            'text': text,
            'voice': current_voice,
            'lang': current_lang_code,
            'is_fallback': False
        }]

    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)

    # 🌟 GLOBAL PRE-SCAN CONTEXT
    has_japanese_kana = bool(re.search(r'[\u3041-\u3096\u30A1-\u30FA]', text))
    has_chinese_kanji = bool(re.search(r'[\u4E00-\u9FFF]', text)) and not has_japanese_kana

    # Standardized CJK + Fullwidth Punctuation blocks
    cjk_pattern = r'([\u3000-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF\u2000-\u206F]+)'
    parts = re.split(cjk_pattern, text)
    
    evaluated_parts = []
    for part in parts:
        if not part: continue
        evaluated_parts.append({
            'text': part,
            'lang': _determine_chunk_lang(part, base_current_lang, has_japanese_kana, has_chinese_kanji)
        })
        
    # 🌟 FIX 1: Two-Pass Nearest Neighbor Language Propagation
    # Pass 1: Right-to-Left (Pulls context backwards for leading numbers/symbols)
    for i in range(len(evaluated_parts) - 2, -1, -1):
        if evaluated_parts[i]['lang'] == 'unknown':
            if evaluated_parts[i+1]['lang'] != 'unknown':
                evaluated_parts[i]['lang'] = evaluated_parts[i+1]['lang']

    # Pass 2: Left-to-Right (Pushes context forwards for trailing numbers/symbols)
    for i in range(1, len(evaluated_parts)):
        if evaluated_parts[i]['lang'] == 'unknown':
            if evaluated_parts[i-1]['lang'] != 'unknown':
                evaluated_parts[i]['lang'] = evaluated_parts[i-1]['lang']

    # Pass 3: Ultimate Fallback (If entire text was pure numbers like "12345")
    for part_dict in evaluated_parts:
        if part_dict['lang'] == 'unknown':
            part_dict['lang'] = base_current_lang
                
    segments = []
    for part_dict in evaluated_parts:
        part = part_dict['text']
        resolved_lang = part_dict['lang']
        
        assigned_voice = current_voice
        is_fallback = False
        
        if resolved_lang != base_current_lang:
            # Disable English fallback. This allows EN to switch to JP, 
            # but prevents native CJK voices from jarringly switching to af_sky.
            if resolved_lang == 'en':
                pass 
            elif resolved_lang in FALLBACK_VOICES:
                assigned_voice = FALLBACK_VOICES[resolved_lang]
                is_fallback = True
                
        clean_part = part
        
        # Hard Quote Vaporization for Asian languages
        if resolved_lang in ('ja', 'cmn') or assigned_voice in (FALLBACK_VOICES.get('ja'), FALLBACK_VOICES.get('cmn')):
            clean_part = re.sub(r'[“”〝〟「」『』≪≫"\'`<>\(\)（）\[\]【】]', '', clean_part)
            
        if clean_part:
            segments.append({
                'text': clean_part,
                'voice': assigned_voice,
                'lang': resolved_lang,
                'is_fallback': is_fallback
            })
            
    # Flawless Intelligent Merger
    merged_segments = []
    for seg in segments:
        if not merged_segments:
            merged_segments.append(seg)
        else:
            last_seg = merged_segments[-1]
            if last_seg['voice'] == seg['voice']:
                space_char = "" if last_seg['lang'] in ('ja', 'cmn') else " "
                
                if last_seg['lang'] in ('ja', 'cmn'):
                    last_seg['text'] = last_seg['text'].rstrip()
                    seg['text'] = seg['text'].lstrip()
                    
                last_seg['text'] += space_char + seg['text']
                last_seg['is_fallback'] = last_seg['is_fallback'] or seg['is_fallback']
            else:
                merged_segments.append(seg)
                
    return merged_segments