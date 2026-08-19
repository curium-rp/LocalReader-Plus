import re
from typing import List, Dict, Any
from num2words import num2words

# ==========================================
# 1. DICTIONARIES & MAPS
# ==========================================

STUTTER_MAP = {
    'b': 'bih', 'c': 'kih', 'd': 'dih', 'f': 'fih', 'g': 'gih', 'h': 'hih', 
    'j': 'jih', 'k': 'kih', 'l': 'lih', 'm': 'mih', 'n': 'nih', 'p': 'pih', 
    'q': 'kwih', 'r': 'rih', 's': 'sih', 't': 'tih', 'v': 'vih', 'w': 'wih', 
    'y': 'yih', 'z': 'zih',
    'a': 'ah', 'e': 'eh', 'i': 'ih', 'o': 'oh', 'u': 'uh',
    'sh': 'shih', 'ch': 'chih', 'th': 'thih', 'ph': 'fih', 'wh': 'wih',
    'br': 'brih', 'cr': 'krih', 'dr': 'drih', 'fr': 'frih', 'gr': 'grih', 'pr': 'prih', 'tr': 'trih',
    'bl': 'blih', 'cl': 'klih', 'fl': 'filih', 'gl': 'glih', 'pl': 'plih', 'sl': 'slih',
    'sc': 'skih', 'sm': 'smih', 'sn': 'snih', 'sp': 'spih', 'st': 'stih', 'sw': 'swih'
}

INTERJECTION_MAP = {
    r'h+m+': 'hum',       
    r'm{2,}': 'uhm',      
    r'u+h+': 'uh',        
    r'rgh+': 'urgh',      
    r'grr+': 'gurr',      
    r'ugh+': 'uhg', 
    r'tch': 'tisk', 
    r'gah': 'gah',
    r'ngh+': 'ung',       
    r'n+g+h+': 'ung',         
    r'oof+': 'oof', 
    r'ack': 'ack', 
    r'urk': 'erk',
    r'hmph': 'humph', 
    r'n{2,}h?': 'uhn',        
    r'm+h+': 'um',            
    r'm+[\-]?p+h+': 'umph',   
    r'pff+t?': 'pufft',   
    r'bah': 'bah', 
    r'tsk(?:\-tsk)?': 'tisk, tisk',
    r'eep': 'eep', 
    r'kyaa+': 'kya', 
    r'hiii+e?': 'heee',   
    r'phew': 'fyoo', 
    r'whew': 'hweo',
    r'hngh+': 'hung',
    r'w+a+h+': 'wah',         
    r'b+r{2,}': 'burr',       
    r's+h{2,}': 'shush',      
    r'p+s+t+': 'pist',        
    r'z{2,}': 'zuh',          
    r'a+w{2,}': 'aw'          
}

# 🌟 THE SHIELD: Exact terms are matched FIRST.
EXACT_STUTTER_MAP = {
    r"\bi-i\b": "I I",
    r"\bi-i-i\b": "I I I",
    r"\bR18\b": "Rated 18"
}

_space_words = [
    "s-series", "s-serious", "a-arm", "a-axis", "b-boy", "b-ball", "b-battery", 
    "c-clamp", "c-class", "c-cup", "c-clef", "c-channel", "c-curve", "d-day", 
    "d-drive", "e-edition", "e-exam", "f-factor", "f-frame", "g-gauge", "g-group", 
    "h-hour", "h-harness", "m-mode", "m-matrix", "n-number", "n-node", "p-phase", 
    "p-protein", "p-port", "r-rating", "r-ratio", "s-scroll", "s-strap", "t-test", 
    "t-track", "t-tube", "t-tool", "v-valve", "w-waveform", "a-rank", "a-frame", 
    "a-list", "a-line", "a-side", "a-team", "a-bomb", "a-pillar", "b-side", "b-movie", 
    "c-section", "e-book", "e-mail", "k-pop", "t-shirt", "u-turn", "v-neck", "x-ray"
]
for w in _space_words:
    EXACT_STUTTER_MAP[rf"\b{w}\b"] = w.replace("-", " ")

_phonetic_stutters = {
    "a-and": "uh and", "a-as": "uh as", "a-at": "uh at", "a-an": "uh an", "a-am": "uh am", 
    "a-about": "uh about", "a-again": "uh again", "a-all": "aw all", "a-always": "aw always", 
    "a-any": "eh any", "a-anyone": "eh anyone", "a-anything": "eh anything", "a-are": "ar are",
    "o-okay": "oh okay", "w-what": "wuh what", "w-why": "wuh why", "w-where": "wuh where", 
    "w-who": "wuh who", "w-when": "wuh when", "th-that": "thuh that", "th-this": "thih this", 
    "th-there": "thuh there", "th-they": "thuh they", "y-you": "yuh you", "y-your": "yuh your", 
    "y-yes": "yuh yes", "n-no": "nuh no", "n-not": "nuh not", "s-so": "suh so", "b-but": "buh but", 
    "b-because": "bih because", "h-how": "huh how", "h-he": "hih he", "sh-she": "shih she", 
    "o-oh": "oh oh", "u-uh": "uh uh"
}
for k, v in _phonetic_stutters.items():
    EXACT_STUTTER_MAP[rf"\b{k}\b"] = v
# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def process_jp_stutter(match):
    prefix = match.group(1)
    name = match.group(2)
    honorific = match.group(3)
    
    vowels = "aeiouAEIOU"
    vowel_index = -1
    
    limit = min(3, len(name))
    for i in range(limit):
        if name[i] in vowels:
            vowel_index = i
            break
            
    if vowel_index != -1:
        raw_prefix = name[:vowel_index + 1].lower()
        
        # 🌟 PHONETIC MAP FOR KOKORO TTS
        # Forces English G2P to pronounce Japanese Romaji vowels accurately
        jp_phonetic_fixes = {
            # -i sounds (Fixes "Mi" -> "My", "Ki" -> "Kai", "Ri" -> "Rye")
            'mi': 'mee', 'ki': 'kee', 'ni': 'nee', 'hi': 'hee', 
            'bi': 'bee', 'pi': 'pee', 'ri': 'ree', 'chi': 'chee',
            'shi': 'shee', 'ji': 'jee', 'zi': 'jee', 'ti': 'tee',
            
            # -a sounds (Fixes "Sa" -> "Say", "Ka" -> "Kay")
            'ma': 'mah', 'ka': 'kah', 'na': 'nah', 'ha': 'hah',
            'ba': 'bah', 'pa': 'pah', 'ra': 'rah', 'sa': 'sah', 'ta': 'tah',
            
            # -e sounds (Fixes "Me" -> "Me", "Se" -> "See")
            'me': 'meh', 'ke': 'keh', 'ne': 'neh', 'he': 'heh',
            'be': 'beh', 'pe': 'peh', 're': 'reh', 'se': 'seh', 'te': 'teh',
            
            # -o sounds
            'mo': 'moh', 'ko': 'koh', 'no': 'noh', 'ho': 'hoh',
            'bo': 'boh', 'po': 'poh', 'ro': 'roh', 'so': 'soh', 'to': 'toh',
            
            # -u sounds
            'mu': 'moo', 'ku': 'koo', 'nu': 'noo', 'hu': 'hoo', 'fu': 'foo',
            'bu': 'boo', 'pu': 'poo', 'ru': 'roo', 'su': 'soo', 'tsu': 'tsoo'
        }
        
        # Get phonetic override or fall back to raw syllable (e.g. 'yu' stays 'yu')
        phonetic_prefix = jp_phonetic_fixes.get(raw_prefix, raw_prefix)
        
        # Preserve original capitalization
        if prefix.isupper():
            phonetic_prefix = phonetic_prefix.capitalize()
        else:
            phonetic_prefix = phonetic_prefix.lower()
            
        return f"{phonetic_prefix} {name}-{honorific}"
        
    return match.group(0)

# ==========================================
# 3. MASTER NORMALIZATION ENGINE
# ==========================================

def fix_broken_words(text: str) -> str:
    """Unified engine that fixes spaces, protects exact words, and processes stutters."""
    if not text:
        return text

    # 1. Ghost Space Cleanups
    text = re.sub(r'([a-zA-Z])\s*([\'’])\s*([a-zA-Z])', r'\1\2\3', text)
    text = re.sub(r'([sS])\s+([\'’])(?=\s|$)', r'\1\2', text)

    ligatures = {'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl', '\ufb05': 'ft', '\ufb06': 'st', '\u00a0': ' ', '\u2013': '-', '\u2014': '--'}
    for char, rep in ligatures.items(): 
        text = text.replace(char, rep)

    text = re.sub(r'(\w+)-\s+(\w+)', r'\1 \2', text)
    
    common = [
        (r'\bo\s+ff\b', 'off'), (r'\bo\s+f\b', 'of'), (r'\ba\s+nd\b', 'and'), 
        (r'\bt\s+he\b', 'the'), (r'\bi\s+n\b', 'in'), (r'\bi\s+t\b', 'it'), 
        (r'\bi\s+s\b', 'is'), (r'\bt\s+o\b', 'to'), (r'\bs\s+t\b', 'st'),
        (r'\.\s+\.\s+\.', '...'), (r'\.\s+\.', '..')
    ]
    for pat, rep in common: 
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    old = ""
    while old != text:
        old = text
        text = re.sub(r'(?:^|(?<=\s))([a-zA-Z])\s+([a-zA-Z])(?=\s|$)', r'\1\2', text)

    # ==========================================
    # 🌟 STEP 2: APPLY EXACT STUTTER MAP FIRST (The Shield)
    # ==========================================
    # This runs BEFORE the dynamic map. It turns "C-class" into "See class".
    # Because there is no hyphen left, the dynamic stutter engine ignores it entirely.
    for pattern, replacement in EXACT_STUTTER_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # ==========================================
    # 🌟 STEP 3: JAPANESE HONORIFIC STUTTERS
    # ==========================================
    jp_pattern = r"\b([A-Za-z])-([A-Za-z]+)-(san|chan|kun|sama|dono|senpai|dano)\b"
    text = re.sub(jp_pattern, process_jp_stutter, text, flags=re.IGNORECASE)

    # ==========================================
    # 🌟 STEP 4: DYNAMIC STUTTER RESOLUTION
    # ==========================================
    def resolve_stutter(match):
        original_prefix = match.group(1)
        remainder_of_word = match.group(2)
        lookup = original_prefix.lower()
        
        # Only maps if the remainder of the word starts with the stuttered letter
        if lookup in STUTTER_MAP and remainder_of_word.lower().startswith(lookup):
            kokoro_fixes = {
                't': 'tuh', 'th': 'thuh', 
                'w': 'wuh', 'wh': 'wuh',
                'b': 'buh', 'd': 'duh', 
                'p': 'puh', 'k': 'kuh', 'c': 'kuh'
            }
            phonetic = kokoro_fixes.get(lookup, STUTTER_MAP[lookup])
            phonetic_cased = phonetic.capitalize() if original_prefix[0].isupper() else phonetic
            return f"{phonetic_cased} {remainder_of_word}"
        
        return f"{original_prefix}-{remainder_of_word}"

    old_stutter = ""
    while old_stutter != text:
        old_stutter = text
        text = re.sub(r'(?<![a-zA-Z0-9\'])([a-zA-Z]{1,3})[-—–]+([a-zA-Z]+)', resolve_stutter, text)

    # ==========================================
    # 🌟 STEP 5: FINAL CLEANUPS
    # ==========================================
    
    # Repeating characters (e.g., Ewww -> Eww)
    text = re.sub(r"([A-Za-z])\1{2,}", r"\1\1", text)
    
    # Trailing cutoffs (e.g., He- -> He...) for better TTS pauses
    text = re.sub(r'\b([a-zA-Z]+)[-—–]+(?=\s|$|[.,!?])', r'\1... ', text)

    # Static Interjections
    for pattern, phonetic_replacement in INTERJECTION_MAP.items():
        text = re.sub(r'\b' + pattern + r'\b', phonetic_replacement, text, flags=re.IGNORECASE)

    # Punctuation Cleanup
    text = re.sub(r'([\"\(\[\{\u201c])\s+', r'\1', text) 
    text = re.sub(r'\s+([\"\)\}\]\u201d])', r'\1', text) 
    
    return re.sub(r'\s+', ' ', text).strip()


def fix_special_formats(text: str, lang: str = "en") -> str:
    """Handles edge cases like time, dates, phone numbers, and currency with a Voice Gate."""
    if not text:
        return text

    # 1. QUOTES & BRACKETS: Remove awkward spacing (Universal for all languages)
    text = re.sub(r'([\"\'\(\[\{\u201c\u2018])\s+', r'\1', text)
    text = re.sub(r'\s+([\"\'\)\}\]\u201d\u2019])', r'\1', text)

    # ==========================================
    # VOICE GATE: Halt English formatting for CJK
    # ==========================================
    if not lang.startswith('en'):
        return text

    # 2. SMART CURRENCY: Handle dollars and optional cents
    def split_currency(match):
        dollars = match.group(1)
        cents = match.group(2)
        if cents and int(cents) > 0:
            return f"{dollars} dollars and {cents} cents"
        return f"{dollars} dollars"
    
    text = re.sub(r'\$([0-9,]+)(?:\.(\d+))?', split_currency, text)

    # 3. TIME: Remove the :00 and strip the periods from a.m. / p.m.
    text = re.sub(r'\b(\d{1,2}):00\b', r'\1', text)
    text = re.sub(r'(\d)\s*(?i:a\.?m\.?)(?=\s|[.,!?]|$)', r'\1 AM', text)
    text = re.sub(r'(\d)\s*(?i:p\.?m\.?)(?=\s|[.,!?]|$)', r'\1 PM', text)

    # 4. DECIMALS: Force digit-by-digit reading after the dot
    def split_decimal(match):
        whole_number = match.group(1)
        decimal_digits = match.group(2)
        spaced_decimals = " ".join(list(decimal_digits))
        return f"{whole_number} point {spaced_decimals}"
    text = re.sub(r'\b(\d+)\.(\d+)\b', split_decimal, text)

    # 5. HYPHENATED NUMBERS: (Phone Numbers & IDs)
    def split_hyphenated(match):
        digit_map = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", 
                     "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
        raw_digits = match.group(0).replace("-", "")
        return " ".join([digit_map.get(d, d) for d in raw_digits])
    text = re.sub(r'\b\d+(?:-\d+){2,}\b', split_hyphenated, text)

    # 6. SMART YEARS: Context-Aware Splitting
    year_pattern = re.compile(
        r'\b('
        r'in|since|from|to|until|through|between|and|during|by|before|after|around|circa|of|year|'
        r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
        r')\s+'
        r'(the\s+|the\s+year\s+|\d{1,2}(?:st|nd|rd|th)?,?\s+)?'
        r'(1[789]\d{2}|20[1-9]\d)(s)?\b',
        re.IGNORECASE
    )
    def split_year(match):
        prefix1 = match.group(1)
        prefix2 = match.group(2) or ""
        year = match.group(3)
        plural = match.group(4) or ""
        return f"{prefix1} {prefix2}{year[:2]} {year[2:]}{plural}"
        
    text = year_pattern.sub(split_year, text)

    return text


def auto_translate_numbers(text: str, lang: str = "en") -> str:
    """Converts numbers to words dynamically, using scanner bounds to protect CJK."""
    if not text:
        return text

    def match_to_words(match):
        raw_string = match.group(0)
        start_idx = match.start()
        end_idx = match.end()
        
        # 1. Forward & Backward Scanner (Isolate the number context)
        left_char = ""
        for char in reversed(text[:start_idx]):
            # 🌟 Added Smart Quotes, Ellipses, and Em-dashes to the ignore list!
            if char.strip() and not re.match(r'[.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–\s]', char):
                left_char = char
                break
                
        right_char = ""
        for char in text[end_idx:]:
            if char.strip() and not re.match(r'[.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–\s]', char):
                right_char = char
                break
        
        # 2. Deactivation Check (STRICTLY Japanese Only)
        # We deleted the broken [^\x00-\x7F] and strictly target Japanese characters.
        ja_regex = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]'
        is_left_ja = bool(re.match(ja_regex, left_char)) if left_char else False
        is_right_ja = bool(re.match(ja_regex, right_char)) if right_char else False
        
        try:
            clean_number = int(raw_string.replace(',', ''))
            
            # 3. Dynamic Native Translation via num2words
            if lang.startswith('ja') or is_left_ja or is_right_ja:
                return num2words(clean_number, lang='ja')
            # 🌟 ZH/CMN DELETED! Chinese fallbacks will now strictly read numbers in English!
            elif lang.startswith('cmn') or lang.startswith('zh') or lang.startswith('en'):
                return num2words(clean_number, lang='en')
            elif lang.startswith('es'):
                return num2words(clean_number, lang='es')
            elif lang.startswith('fr'):
                return num2words(clean_number, lang='fr')
            elif lang.startswith('it'):
                return num2words(clean_number, lang='it')
            elif lang.startswith('pt'):
                return num2words(clean_number, lang='pt_BR')
            elif lang.startswith('hi'):
                # 'hi' or fallback if missing module
                return num2words(clean_number, lang='hi') 
            else:
                return num2words(clean_number, lang='en')
                
        except Exception:
            # If a language isn't supported by num2words (like raw 'hi' on some OS), 
            # it gracefully falls back to leaving the raw digits intact.
            return raw_string

    # Standard \b fails on CJK characters. 
    # Negative Lookarounds perfectly isolate numbers globally.
    return re.sub(r'(?<![\d,])\d+(?:,\d{3})*(?![\d,])', match_to_words, text)
# ==========================================
# ROBUST REGEX & IGNORANCE ENGINE
# ==========================================

def normalize_unicode_quotes(text: str) -> str:
    """Standardizes all smart quotes and apostrophes to standard ASCII characters."""
    if not text: 
        return text
    # Normalize single quotes
    text = text.replace('‘', "'").replace('’', "'").replace('´', "'").replace('`', "'")
    # Normalize double quotes
    text = text.replace('“', '"').replace('”', '"')
    return text

# ==========================================
# CJK MIXED LANGUAGE SHIELD
# ==========================================

def protect_japanese_mixed_latin(text: str, lang: str) -> str:
    """
    Applies native Japanese reading logic to mixed English text.
    Silences redundant bracketed metadata and converts acronyms to Katakana.
    """
    if not lang.startswith('ja'):
        return text
        
    # 1. BRACKETED METADATA SILENCER
    # Native Japanese readers skip English/Acronyms in brackets if they immediately follow a Japanese word.
    # e.g., "国際宇宙ステーション(ISS)" -> "国際宇宙ステーション"
    # e.g., "プログラム (Commercial Crew Program)" -> "プログラム"
    text = re.sub(
        r'([\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]+)(?:\s|\u3000)*[\(\[\{（【「『≪<]+[a-zA-Z0-9\s\-\.,\uFF21-\uFF5A]+[\)\]\}）】」』≫>]+',
        r'\1',
        text
    )
        
    latin_to_kana = {
        'A': 'エー', 'B': 'ビー', 'C': 'シー', 'D': 'ディー', 'E': 'イー', 
        'F': 'エフ', 'G': 'ジー', 'H': 'エイチ', 'I': 'アイ', 'J': 'ジェー', 
        'K': 'ケー', 'L': 'エル', 'M': 'エム', 'N': 'エン', 'O': 'オー', 
        'P': 'ピー', 'Q': 'キュー', 'R': 'アール', 'S': 'エス', 'T': 'ティー', 
        'U': 'ユー', 'V': 'ブイ', 'W': 'ダブリュー', 'X': 'エックス', 'Y': 'ワイ', 'Z': 'ゼット',
        'a': 'エー', 'b': 'ビー', 'c': 'シー', 'd': 'ディー', 'e': 'イー', 
        'f': 'エフ', 'g': 'ジー', 'h': 'エイチ', 'i': 'アイ', 'j': 'ジェー', 
        'k': 'ケー', 'l': 'エル', 'm': 'エム', 'n': 'エン', 'o': 'オー', 
        'p': 'ピー', 'q': 'キュー', 'r': 'アール', 's': 'エス', 't': 'ティー', 
        'u': 'ユー', 'v': 'ブイ', 'w': 'ダブリュー', 'x': 'エックス', 'y': 'ワイ', 'z': 'ゼット',
        # Full-Width Character Support (Ａ-Ｚ, ａ-ｚ)
        'Ａ': 'エー', 'Ｂ': 'ビー', 'Ｃ': 'シー', 'Ｄ': 'ディー', 'Ｅ': 'イー', 
        'Ｆ': 'エフ', 'Ｇ': 'ジー', 'Ｈ': 'エイチ', 'Ｉ': 'アイ', 'Ｊ': 'ジェー', 
        'Ｋ': 'ケー', 'Ｌ': 'エル', 'Ｍ': 'エム', 'Ｎ': 'エン', 'Ｏ': 'オー', 
        'Ｐ': 'ピー', 'Ｑ': 'キュー', 'Ｒ': 'アール', 'Ｓ': 'エス', 'Ｔ': 'ティー', 
        'Ｕ': 'ユー', 'Ｖ': 'ブイ', 'Ｗ': 'ダブリュー', 'Ｘ': 'エックス', 'Ｙ': 'ワイ', 'Ｚ': 'ゼット',
        'ａ': 'エー', 'ｂ': 'ビー', 'ｃ': 'シー', 'ｄ': 'ディー', 'ｅ': 'イー', 
        'ｆ': 'エフ', 'ｇ': 'ジー', 'ｈ': 'エイチ', 'ｉ': 'アイ', 'ｊ': 'ジェー', 
        'ｋ': 'ケー', 'ｌ': 'エル', 'ｍ': 'エム', 'ｎ': 'エン', 'ｏ': 'オー', 
        'ｐ': 'ピー', 'ｑ': 'キュー', 'ｒ': 'アール', 'ｓ': 'エス', 'ｔ': 'ティー', 
        'ｕ': 'ユー', 'ｖ': 'ブイ', 'ｗ': 'ダブリュー', 'ｘ': 'エックス', 'ｙ': 'ワイ', 'ｚ': 'ゼット'
    }
    
    def replace_acronyms(match):
        acronym = match.group(1)
        return "".join([latin_to_kana.get(char, char) for char in acronym])
        
    # 2. Match ANY length of UPPERCASE letters (e.g., "X", "ISS", "USB", "ＩＳＳ")
    # Uses Negative Lookarounds to safely extract acronyms even if wrapped in brackets
    text = re.sub(r'(?<![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])([A-Z\uFF21-\uFF3A]+)(?![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])', replace_acronyms, text)
    
    # 3. Match single lowercase letters (e.g., "x" in "スペースx")
    text = re.sub(r'(?<![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])([a-z\uFF41-\uFF5A])(?![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])', replace_acronyms, text)
    
    return text

def apply_custom_pronunciations(text: str, rules: List[Dict[str, Any]], ignore_list: List[str] = [], lang: str = "en") -> str:
    """
    Applies custom user rules and ignore lists safely and robustly.
    Incorporates Unicode normalization to prevent smart-quote mismatching.
    """
    # 1. Run automatic text fixes first
    text = fix_broken_words(text)
    text = auto_translate_numbers(text, lang)
    
    # 2. Protect mixed Japanese text from triggering English fallbacks
    text = protect_japanese_mixed_latin(text, lang)

    if not rules and not ignore_list:
        return text

    # 2. Normalize text quotes for bulletproof matching
    text = normalize_unicode_quotes(text)

    # 3. Apply Ignore List Safely
    for item in ignore_list:
        if not item: 
            continue
        
        # Normalize the user's ignore string
        clean_item = normalize_unicode_quotes(str(item))
        escaped_item = re.escape(clean_item)
        
        # Robust word boundary for ignore items (prevents partial word deletion like "he" in "the")
        # Uses Negative Lookarounds to perfectly support punctuation marks.
        start_bound = r'(?<!\w)' if clean_item[0].isalnum() else r''
        end_bound = r'(?!\w)' if clean_item[-1].isalnum() else r''
        
        pat = f"{start_bound}{escaped_item}{end_bound}"
        text = re.sub(pat, "", text, flags=re.IGNORECASE)

    # 4. Apply Pronunciation Rules
    for rule in rules:
        orig = rule.get("original", "")
        rep = rule.get("replacement", "")
        if not orig: 
            continue

        clean_orig = normalize_unicode_quotes(str(orig))
        match_case = rule.get("match_case", False)
        word_boundary = rule.get("word_boundary", True)
        is_regex = rule.get("is_regex", False)

        flags = 0 if match_case else re.IGNORECASE

        if is_regex:
            # Trust the user's regex, attempt to execute it safely
            try:
                text = re.sub(clean_orig, str(rep), text, flags=flags)
            except re.error as e:
                print(f"[Regex Error] Rule '{clean_orig}' failed: {e}")
                continue
        else:
            escaped_orig = re.escape(clean_orig)
            
            if word_boundary:
                # Smart Boundaries: Uses lookarounds instead of \b to perfectly handle punctuation
                start_bound = r'(?<!\w)' if clean_orig[0].isalnum() else r''
                end_bound = r'(?!\w)' if clean_orig[-1].isalnum() else r''
                pat = f"{start_bound}{escaped_orig}{end_bound}"
            else:
                pat = escaped_orig
                
            text = re.sub(pat, str(rep), text, flags=flags)

    # Strip double spaces created by ignore list deletions to keep text clean
    return re.sub(r'\s+', ' ', text).strip()


def inject_pauses(text: str, pause_settings: Dict[str, int]) -> str:
    """Placeholder for future TTS engines that support SSML."""
    return text