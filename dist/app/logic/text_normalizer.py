import re
from typing import List, Dict, Any
from num2words import num2words
from functools import lru_cache

try:
    from phonemizer import phonemize
    _has_phonemizer = True
except ImportError as e:
    print(f"[Warn] Phonemizer fail: {e}")
    _has_phonemizer = False

# ==========================================
# 1. DICTIONARIES & MAPS
# ==========================================

HARD_G_WORDS = (
    'get', 'give', 'girl', 'gift', 'gear', 'geese', 'giggle', 
    'girth', 'gill', 'gimmick', 'geek', 'gecko', 'giga'
)

STUTTER_MAP = {
    'b': 'bih', 'c': 'kih', 'd': 'dih', 'f': 'fih', 'g': 'guh', 'h': 'huh', 
    'j': 'jih', 'k': 'kih', 'l': 'lih', 'm': 'mih', 'n': 'nih', 'p': 'pih',
    'q': 'kwih', 'r': 'rih', 's': 'sih', 't': 'tih', 'v': 'vih', 'w': 'wuh', 
    'x': 'zih', 'y': 'yih', 'z': 'zih',
    'a': 'ah', 'e': 'eh', 'i': 'ih', 'o': 'oh', 'u': 'uhh',
    'sh': 'shih', 'ch': 'chih', 'th': 'thih', 'ph': 'fih', 'wh': 'wuh', 'rh': 'rih',
    'sk': 'skih', 'ts': 'tsih',
    'br': 'brih', 'cr': 'krih', 'dr': 'drih', 'fr': 'frih', 'gr': 'grih', 'pr': 'prih', 'tr': 'trih',
    'bl': 'blih', 'cl': 'klih', 'fl': 'flih', 'gl': 'glih', 'pl': 'plih', 'sl': 'slih',
    'sc': 'skih', 'sm': 'smih', 'sn': 'snih', 'sp': 'spih', 'st': 'stih', 'sw': 'swih',
    'str': 'strih', 'spr': 'sprih', 'scr': 'skrih', 'spl': 'splih', 'shr': 'shrih', 
    'thr': 'thrih', 'squ': 'skwih', 'sch': 'skih',
    'bw': 'bwuh', 'mw': 'mwuh'
}

CONTEXT_MAP = {
    'a': {
        'ipa': {'eɪ': 'ay', 'æ': 'ah', 'ə': 'uhh', 'ɑ': 'ah', 'a': 'ah'},
        'text': lambda w: 'ay' if w.startswith(('ac', 'ap', 'av', 'ag', 'at', 'al')) and len(w) >= 3 and w.endswith('e') else 'ah'
    },
    'e': {
        'ipa': {'i': 'ee', 'iː': 'ee', 'ɛ': 'eh', 'e': 'eh'},
        'text': lambda w: 'ee' if w.startswith(('eve', 'equ', 'ea', 'ee')) and not w.startswith(('ever', 'every')) else 'eh'
    },
    'i': {
        'ipa': {'aɪ': 'eye', 'ɪ': 'ih'},
        'text': lambda w: 'eye' if w.startswith(('ic', 'id', 'ir', 'is')) and len(w) >= 3 and w.endswith('e') else 'ih'
    },
    'o': {
        'ipa': {'oʊ': 'oh', 'əʊ': 'oh', 'ɑ': 'aw', 'ɒ': 'aw', 'ɔ': 'aw'},
        'text': lambda w: 'aw' if w.startswith(('on', 'off', 'ox', 'odd', 'opt', 'oct')) else 'oh'
    },
    'u': {
        'ipa': {'u': 'oo', 'ju': 'yoo', 'ʌ': 'uhh', 'ə': 'uhh', 'ɐ': 'uhh'},
        'text': lambda w: 'yoo' if w.startswith(('uni', 'use', 'uta', 'uro')) else 'uhh'
    },
    'c': {
        'ipa': {'s': 'sih', 'k': 'kih', 'tʃ': 'chih', 'ʃ': 'shih'},
        'text': lambda w: 'sih' if w.startswith(('ce', 'ci', 'cy')) else ('chih' if w.startswith('ch') and not w.startswith(('chaos', 'chord', 'chorus', 'chrome', 'chronic', 'chef')) else 'kih')
    },
    'ch': {
        'ipa': {'k': 'kih', 'ʃ': 'shih', 'tʃ': 'chih'},
        'text': lambda w: 'kih' if w.startswith(('chaos', 'chord', 'chorus', 'chrome', 'chronic', 'charisma', 'choir')) else ('shih' if w.startswith(('chef', 'champagne', 'chiffon')) else 'chih')
    },
    'g': {
        'ipa': {'dʒ': 'jih', 'g': 'guh', 'ɡ': 'guh'},
        'text': lambda w: 'jih' if w.startswith(('ge', 'gi', 'gy')) and not w.startswith(HARD_G_WORDS) else 'guh'
    },
    'h': {
        'text': lambda w: 'huh'
    },
    's': {
        'ipa': {'ʃ': 'shih', 's': 'sih', 'z': 'zih'},
        'text': lambda w: 'shih' if w.startswith(('sur', 'sug', 'sh')) else 'sih'
    },
    'sc': {
        'ipa': {'s': 'sih', 'sk': 'skih'},
        'text': lambda w: 'sih' if w.startswith(('scen', 'scie', 'scis', 'scyt', 'scin')) else 'skih'
    }
}

IPA_MAP = {
    'ð': 'thih', 'θ': 'thih', 'k': 'kuh', 's': 'sih', 'dʒ': 'jih', 'tʃ': 'chih',
    'w': 'wuh', 'j': 'yuh', 'm': 'muh', 'n': 'nuh', 'b': 'buh', 'p': 'puh',
    'd': 'duh', 't': 'tuh', 'g': 'guh', 'ɡ': 'guh', 'h': 'huh', 'ʃ': 'shih', 'ʒ': 'zhuh',
    'v': 'vih', 'z': 'zih', 'f': 'fih', 'ɹ': 'rih', 'r': 'rih', 'l': 'lih',
    'æ': 'ah', 'ɛ': 'eh', 'ɪ': 'ih', 'ʌ': 'uhh', 'ɒ': 'aw', 'ɔ': 'aw', 'ə': 'uhh',
    'eɪ': 'ay', 'i': 'ee', 'iː': 'ee', 'aɪ': 'eye', 'aʊ': 'ow', 'ɔɪ': 'oy', 'oʊ': 'oh',
    'u': 'oo', 'uː': 'oo', 'ju': 'yoo', 'ɑ': 'ah', 'ɑː': 'ah', 'ɔː': 'aw', 'ɜ': 'er', 'ɜː': 'er'
}

INTERJECTION_MAP = {
    r'h+m+': 'hum', r'm{2,}': 'uhm', r'u+h+': 'um', r'rgh+': 'urgh', r'grr+': 'gurr',      
    r'ugh+': 'uhg', r'tch': 'tisk', r'ngh+': 'ung', r'n+g+h+': 'ung', r'(?:u+h{2,}|u{2,}h+)': 'uhm',         
    r'oof+': 'oof', r'urk': 'erk', r'hmph': 'humph', r'n{2,}h?': 'uhn',        
    r'm+h+': 'um', r'm+[\-]?p+h+': 'umph', r'pff+t?': 'pufft', 
    r'tsk(?:\-tsk)?': 'tisk, tisk', r'kyaa+': 'kya', r'hiii+e?': 'heee',   
    r'phew': 'fyoo', r'whew': 'hyoo', r'hngh+': 'hung', r'w+a+h+': 'wah',         
    r'b+r{2,}': 'burr', r's+h{2,}': 'shush', r'p+s+t+': 'pist', r'z{2,}': 'zuh',          
    r'a+w{2,}': 'aw'          
}

EXACT_REPLACEMENTS = {
    r"\bR18\b": "Rated 18",
    r"\bi-i\b": "I I",
    r"\bi-i-i\b": "I I I",
    r"\bi-i-i-i\b": "I I I I"
}

STUTTER_EXCEPTIONS = {
    "a-arm", "a-axis",
    "b-ball", "b-battery", "b-beam", "b-block", "b-blocker", "b-box", "b-boy", "b-branch",
    "c-cell", "c-chain", "c-channel", "c-circuit", "c-clamp", "c-class", "c-clef", "c-clip", "c-code", "c-core", "c-cup", "c-curve",
    "d-data", "d-day", "d-delay", "d-disk", "d-domain", "d-drive",
    "e-edition", "e-engine", "e-entry", "e-error", "e-event", "e-exam",
    "f-factor", "f-field", "f-file", "f-filter", "f-flag", "f-flow", "f-form", "f-frame", "f-frequency", "f-function",
    "g-gas", "g-gauge", "g-grade", "g-grid", "g-group", "g-guard", "g-guide",
    "h-harness", "h-header", "h-hole", "h-host", "h-hour", "h-hub",
    "j-joint", "j-junction",
    "k-key",
    "l-label", "l-layer", "l-level", "l-line", "l-link", "l-list", "l-lock", "l-loop",
    "m-matrix", "m-mode", "m-module",
    "n-network", "n-node", "n-number",
    "p-packet", "p-path", "p-phase", "p-pin", "p-pipe", "p-plane", "p-point", "p-pool", "p-port", "p-protein",
    "q-query", "q-queue",
    "r-range", "r-rank", "r-rate", "r-rating", "r-ratio", "r-record", "r-register", "r-ring", "r-rod", "r-route", "r-rule",
    "s-scale", "s-scope", "s-score", "s-scroll", "s-section", "s-series", "s-serious", "s-set", "s-side", "s-signal", "s-stage", "s-state", "s-strap", "s-stream", "s-string", "s-switch",
    "t-table", "t-tag", "t-target", "t-team", "t-term", "t-test", "t-thread", "t-tier", "t-token", "t-tool", "t-track", "t-tree", "t-tube", "t-type",
    "v-valve", "v-value", "v-vector", "v-view", "v-voltage",
    "w-waveform", "w-weight", "w-wire", "w-word",
    "x-xylophone",
    "z-zero", "z-zone"
}

STUTTER_REMOVE = {
    "he", "it", "its", "an", "and", "we", "if",
    "who", "whom", "whose", "whoever", "whomever", "whole", "wholly"
} 

LIMIT_STUTTER = {
    'a': set(),
    'e': set(),
    'i': set(),
    'o': {'oh', 'ohh', 'ooh', 'oops', 'ouch', 'oof', 'ow', 'okay', 'ok', 'oy', 'oi'},
    'u': set(),
    'y': set(),
    'x': set(),
    'q': set()
}

EXPECTED_FIRST_PHONEME = {
    'b': ('b',), 'c': ('k', 's', 'tʃ', 'ʃ', 'ts'), 'd': ('d', 'dʒ'),
    'f': ('f',), 'g': ('g', 'ɡ', 'dʒ', 'ʒ'), 
    'h': ('h',),
    'j': ('dʒ', 'ʒ', 'h', 'j'), 'k': ('k',), 'l': ('l', 'j'), 'm': ('m',),
    'n': ('n', 'ɲ'), 'p': ('p', 'f'), 'q': ('k',), 'r': ('ɹ', 'r', 'ɾ'),
    's': ('s', 'z', 'ʃ', 'ʒ'), 't': ('t', 'θ', 'ð', 'tʃ', 'ʃ', 's', 'ts'),
    'v': ('v',), 'w': ('w', 'v', 'h'), 'x': ('z', 'k', 's'), 'y': ('j', 'ɪ', 'i', 'aɪ'),
    'z': ('z', 'ʒ', 's')
}

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

@lru_cache(maxsize=1024)
def get_ipa_sound(word: str) -> str:
    """Cache phonemizer calls. Return mapped grapheme string."""
    if not _has_phonemizer:
        return ""
    
    try:
        ipa = phonemize(word, language='en-us', backend='espeak', with_stress=False, preserve_punctuation=False).strip()
    except Exception:
        return ""
        
    if not ipa:
        return ""
        
    clean_ipa = re.sub(r'[ˈˌː\.\u0325]', '', ipa)
    if not clean_ipa:
        return ""
        
    if len(clean_ipa) >= 2:
        sound_2 = clean_ipa[0:2]
        if sound_2 in IPA_MAP:
            return IPA_MAP[sound_2]
            
    sound_1 = clean_ipa[0]
    if sound_1 in IPA_MAP:
        return IPA_MAP[sound_1]
        
    return ""

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
        
        jp_phonetic_fixes = {
            'mi': 'mee', 'ki': 'kee', 'ni': 'nee', 'hi': 'hee', 
            'bi': 'bee', 'pi': 'pee', 'ri': 'ree', 'chi': 'chee',
            'shi': 'shee', 'ji': 'jee', 'zi': 'jee', 'ti': 'tee',
            'ma': 'mah', 'ka': 'kah', 'na': 'nah', 'ha': 'hah',
            'ba': 'bah', 'pa': 'pah', 'ra': 'rah', 'sa': 'sah', 'ta': 'tah',
            'me': 'meh', 'ke': 'keh', 'ne': 'neh', 'he': 'heh',
            'be': 'beh', 'pe': 'peh', 're': 'reh', 'se': 'seh', 'te': 'teh',
            'mo': 'moh', 'ko': 'koh', 'no': 'noh', 'ho': 'hoh',
            'bo': 'boh', 'po': 'poh', 'ro': 'roh', 'so': 'soh', 'to': 'toh',
            'mu': 'moo', 'ku': 'koo', 'nu': 'noo', 'hu': 'hoo', 'fu': 'foo',
            'bu': 'boo', 'pu': 'poo', 'ru': 'roo', 'su': 'soo', 'tsu': 'tsoo'
        }
        
        phonetic_prefix = jp_phonetic_fixes.get(raw_prefix, raw_prefix)
        
        if prefix.isupper():
            phonetic_prefix = phonetic_prefix[0].upper() + phonetic_prefix[1:]
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

    text = re.sub(r'([a-zA-Z])\s*([\'’])\s*([a-zA-Z])', r'\1\2\3', text)
    text = re.sub(r'([sS])\s+([\'’])(?=\s|$)', r'\1\2', text)

    ligatures = {
        '\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', 
        '\ufb04': 'ffl', '\ufb05': 'ft', '\ufb06': 'st', '\u00a0': ' ', 
        '\u2013': '-', '\u2014': '--'
    }
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

    for pattern, replacement in EXACT_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    jp_pattern = r"\b([A-Za-z])-([A-Za-z]+)-(san|chan|kun|sama|dono|senpai|sensei|dano)\b"
    text = re.sub(jp_pattern, process_jp_stutter, text, flags=re.IGNORECASE)

    def resolve_stutter(match):
        pre_punct = match.group(1)
        raw_prefixes = match.group(2)
        remainder_of_word = match.group(3)
        rem_low = remainder_of_word.lower()
        
        prefixes = [p for p in re.split(r'[-—–]+', raw_prefixes) if p]
        
        # 1. Prefix match validation
        if not all(rem_low.startswith(p.lower()) for p in prefixes):
            return match.group(0)
            
        # 2. Exception shield for single-prefix compound nouns
        if len(prefixes) == 1:
            full_stutter_key_first = f"{prefixes[0].lower()}-{rem_low}"
            if full_stutter_key_first in STUTTER_EXCEPTIONS:
                return match.group(0)
            
        first_prefix = prefixes[0].lower()
        first_char = first_prefix[0]
        
        # Suppress disabled words and collapse non-whitelisted letter stutters
        if rem_low in STUTTER_REMOVE:
            return f"{pre_punct}{remainder_of_word}"
            
        if first_char in LIMIT_STUTTER and rem_low not in LIMIT_STUTTER[first_char]:
            return f"{pre_punct}{remainder_of_word}"
            
        # 4. Silent letter check
        is_silent = False
        if rem_low.startswith(('kn', 'gn', 'pn', 'ps', 'pt', 'wr', 'cz', 'mn')) and first_char == rem_low[0]:
            is_silent = True
            
        first_phoneme = ""
        if _has_phonemizer:
            try:
                ipa = phonemize(rem_low, language='en-us', backend='espeak', with_stress=False, preserve_punctuation=False).strip()
                clean_ipa = re.sub(r'[ˈˌː\.\u0325]', '', ipa)
                if clean_ipa:
                    first_phoneme = clean_ipa[:2] if len(clean_ipa) >= 2 and clean_ipa.startswith(('dʒ', 'tʃ', 'eɪ', 'oʊ', 'aɪ', 'aʊ', 'ɔɪ', 'ju', 'iː')) else clean_ipa[0]
                    if not is_silent:
                        expected_sounds = EXPECTED_FIRST_PHONEME.get(first_char)
                        if expected_sounds and not clean_ipa.startswith(expected_sounds):
                            is_silent = True
            except Exception:
                pass
                
        if is_silent:
            return f"{pre_punct}{remainder_of_word}"
            
        # 5. Phonetic translation 
        result = []
        cluster = rem_low[:2] if len(rem_low) >= 2 else ""
        
        for p in prefixes:
            lookup = p.lower()
            ctx = CONTEXT_MAP.get(lookup, {})
            
            smart_sound = None
            if first_phoneme and 'ipa' in ctx:
                smart_sound = ctx['ipa'].get(first_phoneme)
            if not smart_sound and 'text' in ctx:
                smart_sound = ctx['text'](rem_low)
            
            mapped_sound = None
            if smart_sound:
                mapped_sound = smart_sound
            elif len(lookup) == 1 and cluster in STUTTER_MAP and cluster[0] == lookup:
                mapped_sound = STUTTER_MAP[cluster]
            elif lookup in STUTTER_MAP:
                mapped_sound = STUTTER_MAP[lookup]
            else:
                mapped_sound = get_ipa_sound(rem_low)
                if not mapped_sound:
                    mapped_sound = f"{lookup}uhh"

            if p[0].isupper():
                result.append(mapped_sound[0].upper() + mapped_sound[1:])
            else:
                result.append(mapped_sound)
                
        result.append(remainder_of_word)
        return f"{pre_punct}{' '.join(result)}"

    text = re.sub(r'(?<![a-zA-Z])([\'"“‘\[\(\{]*)((?:[a-zA-Z]{1,3}[-—–]+)+)([a-zA-Z]+(?:\'[a-zA-Z]+)?)', resolve_stutter, text)
    text = re.sub(r"([A-Za-z])\1{2,}", r"\1\1", text)

    for pattern, phonetic_replacement in INTERJECTION_MAP.items():
        text = re.sub(r'\b' + pattern + r'\b', phonetic_replacement, text, flags=re.IGNORECASE)

    text = re.sub(r'([\"\(\[\{\u201c])\s+', r'\1', text) 
    text = re.sub(r'\s+([\"\)\}\]\u201d])', r'\1', text) 
    
    return re.sub(r'\s+', ' ', text).strip()


def fix_special_formats(text: str, lang: str = "en") -> str:
    """Handles edge cases like time, dates, phone numbers, currency, and paper sizes."""
    if not text:
        return text

    text = re.sub(r'([\"\'\(\[\{\u201c\u2018])\s+', r'\1', text)
    text = re.sub(r'\s+([\"\'\)\}\]\u201d\u2019])', r'\1', text)

    # Hyphenated capital forces Kokoro G2P to pronounce letter name /eI/ without pause
    text = re.sub(r'\b[Aa](\d+)\b', r'A-\1', text)

    if not lang.startswith('en'):
        return text

    def split_currency(match):
        dollars = match.group(1)
        cents = match.group(2)
        if cents and int(cents) > 0:
            return f"{dollars} dollars and {cents} cents"
        return f"{dollars} dollars"
    
    text = re.sub(r'\$([0-9,]+)(?:\.(\d+))?', split_currency, text)

    # Dot-separated time with AM/PM (e.g. 3.43 P.M. -> 3:43 pm) before decimal engine fires
    text = re.sub(r'\b(\d{1,2})\.(\d{2})\s*(?i:a\.?m\.?)(?=\s|[.,!?]|$)', r'\1:\2 am', text)
    text = re.sub(r'\b(\d{1,2})\.(\d{2})\s*(?i:p\.?m\.?)(?=\s|[.,!?]|$)', r'\1:\2 pm', text)

    # Normalize standalone time markers to lowercase
    text = re.sub(r'\b(\d{1,2}):00\b', r'\1', text)
    text = re.sub(r'(\d)\s*(?i:a\.?m\.?)(?=\s|[.,!?]|$)', r'\1 am', text)
    text = re.sub(r'(\d)\s*(?i:p\.?m\.?)(?=\s|[.,!?]|$)', r'\1 pm', text)

    def split_decimal(match):
        whole_number = match.group(1)
        decimal_digits = match.group(2)
        spaced_decimals = " ".join(list(decimal_digits))
        return f"{whole_number} point {spaced_decimals}"
    text = re.sub(r'\b(\d+)\.(\d+)\b', split_decimal, text)

    def split_hyphenated(match):
        digit_map = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", 
                     "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
        raw_digits = match.group(0).replace("-", "")
        return " ".join([digit_map[d] if d in digit_map else d for d in raw_digits])
    text = re.sub(r'\b\d+(?:-\d+){2,}\b', split_hyphenated, text)

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
    """Converts numbers to words dynamically, supporting ordinals and CJK scanner bounds."""
    if not text:
        return text

    def match_to_words(match):
        raw_number = match.group(1)
        suffix = match.group(2)
        raw_string = match.group(0)
        start_idx = match.start()
        end_idx = match.end()
        
        left_char = ""
        for char in reversed(text[:start_idx]):
            if char.strip() and not re.match(r'[.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–\s]', char):
                left_char = char
                break
                
        right_char = ""
        for char in text[end_idx:]:
            if char.strip() and not re.match(r'[.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–\s]', char):
                right_char = char
                break
        
        ja_regex = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]'
        is_left_ja = bool(re.match(ja_regex, left_char)) if left_char else False
        is_right_ja = bool(re.match(ja_regex, right_char)) if right_char else False
        
        try:
            clean_number = int(raw_number.replace(',', ''))
            
            target_lang = 'en'
            if lang.startswith('ja') or is_left_ja or is_right_ja:
                target_lang = 'ja'
            elif lang.startswith('es'):
                target_lang = 'es'
            elif lang.startswith('fr'):
                target_lang = 'fr'
            elif lang.startswith('it'):
                target_lang = 'it'
            elif lang.startswith('pt'):
                target_lang = 'pt_BR'
            elif lang.startswith('hi'):
                target_lang = 'hi'
            
            if suffix:
                return num2words(clean_number, lang=target_lang, to='ordinal')
            else:
                return num2words(clean_number, lang=target_lang)
                
        except Exception:
            return raw_string

    return re.sub(r'(?<![\d,])(\d+(?:,\d{3})*)(st|nd|rd|th)?\b', match_to_words, text, flags=re.IGNORECASE)


def normalize_unicode_quotes(text: str) -> str:
    """Standardizes all smart quotes and apostrophes to standard ASCII characters."""
    if not text: 
        return text
    text = text.replace('‘', "'").replace('’', "'").replace('´', "'").replace('`', "'")
    text = text.replace('“', '"').replace('”', '"')
    return text


def protect_japanese_mixed_latin(text: str, lang: str) -> str:
    """Applies native Japanese reading logic to mixed English text."""
    if not lang.startswith('ja'):
        return text
        
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
        
    text = re.sub(r'(?<![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])([A-Z\uFF21-\uFF3A]+)(?![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])', replace_acronyms, text)
    text = re.sub(r'(?<![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])([a-z\uFF41-\uFF5A])(?![a-zA-Z\uFF21-\uFF3A\uFF41-\uFF5A])', replace_acronyms, text)
    
    return text


def apply_custom_pronunciations(text: str, rules: List[Dict[str, Any]], ignore_list: List[str] = None, lang: str = "en") -> str:
    if ignore_list is None:
        ignore_list = []

    text = normalize_unicode_quotes(text)

    for item in ignore_list:
        if not item: 
            continue
        
        clean_item = normalize_unicode_quotes(str(item))
        escaped_item = re.escape(clean_item)
        
        start_bound = r'(?<!\w)' if clean_item[0].isalnum() else r''
        end_bound = r'(?!\w)' if clean_item[-1].isalnum() else r''
        
        pat = f"{start_bound}{escaped_item}{end_bound}"
        text = re.sub(pat, "", text, flags=re.IGNORECASE)

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
            try:
                text = re.sub(clean_orig, str(rep), text, flags=flags)
            except re.error as e:
                print(f"[Regex Error] Rule '{clean_orig}' failed: {e}")
                continue
        else:
            escaped_orig = re.escape(clean_orig)
            
            if word_boundary:
                start_bound = r'(?<!\w)' if clean_orig[0].isalnum() else r''
                end_bound = r'(?!\w)' if clean_orig[-1].isalnum() else r''
                pat = f"{start_bound}{escaped_orig}{end_bound}"
            else:
                pat = escaped_orig
                
            text = re.sub(pat, str(rep), text, flags=flags)

    text = fix_broken_words(text)
    text = fix_special_formats(text, lang)
    text = auto_translate_numbers(text, lang)
    text = protect_japanese_mixed_latin(text, lang)

    return re.sub(r'\s+', ' ', text).strip()


def inject_pauses(text: str, pause_settings: Dict[str, int]) -> str:
    """Placeholder for future TTS engines that support SSML."""
    return text