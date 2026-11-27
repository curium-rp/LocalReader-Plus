import re
from typing import List, Dict, Any
from num2words import num2words
from functools import lru_cache

try:
    from .syllable import phonemize, HAS_PHONEMIZER as _has_phonemizer
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
    'd': 'dih', 'f': 'fih', 'g': 'guh', 'h': 'huh', 
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
    'b': {
        # Preserve existing cluster sounds (br/bl/bw) outside the listed exception.
        'text': lambda w: 'buh' if w.startswith(('butter',)) else STUTTER_MAP.get(w[:2], 'bih')
    },
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
        'ipa': {'s': 'sih', 'k': 'kuh', 'tʃ': 'chih', 'ʃ': 'shih'},
        'text': lambda w: 'sih' if w.startswith(('ce', 'ci', 'cy')) else ('chih' if w.startswith('ch') and not w.startswith(('chaos', 'chord', 'chorus', 'chrome', 'chronic', 'chef')) else 'kuh')
    },
    'ch': {
        'ipa': {'k': 'kuh', 'ʃ': 'shih', 'tʃ': 'chih'},
        'text': lambda w: 'kuh' if w.startswith(('chaos', 'chord', 'chorus', 'chrome', 'chronic', 'charisma', 'choir')) else ('shih' if w.startswith(('chef', 'champagne', 'chiffon')) else 'chih')
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

# Order matters: specific forms before the general ones that would swallow them (uhh before uh, mm-hmm before hmm)
INTERJECTION_MAP = {
    r'm+-?h+m+': 'mhm',r'(?:u+h{2,}|u{2,}h+)': 'uhm',
    r'h+m+': 'hum', r'm{2,}': 'uhm', r'u+h+': 'um', r'rgh+': 'urgh', r'grr+': 'gurr',      
    r'ugh+': 'uhg', r'tch': 'tisk', r'ngh+': 'ung', r'n+g+h+': 'ung',
    r'oof+': 'oof', r'urk': 'erk', r'hmph': 'humph', r'n{2,}h?': 'uhn',        
    r'm+h+': 'um', r'm+[\-]?p+h+': 'umph', r'pff+t?': 'pufft', 
    r'tsk(?:\-tsk)?': 'tisk, tisk', r'kyaa+': 'kya', r'hiii+e?': 'heee',   
    r'phew': 'fyoo', r'whew': 'hyoo', r'hngh+': 'hung', r'w+a+h+': 'wah',         
    r'b+r{2,}': 'burr', r's+h{2,}': 'shush', r'p+s+t+': 'pist', r'z{2,}': 'zuh',          
    r'a+w{2,}': 'aw'          
}

EXACT_REPLACEMENTS = {
    r"\bR18\b": "Rated 18",
    r"\bC'mere\b": "kuh-MEER",
    r"\bth(?:\.{2,}|…)\s*thank\b": "thih-thank",
    # Longest first, or "i-i" eats the start of "i-i-i"
    r"\bi-i-i-i\b": "I I I I",
    r"\bi-i-i\b": "I I I",
    r"\bi-i\b": "I I"
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

QUOTE_OPEN = r'["\'“‘]'
QUOTE_CLOSE = r'["\'”’]'
ROMAN_NUMERAL = re.compile(r'^(?=[MDCLXVI])M*(C[MD]|D?C{0,3})(X[CL]|L?X{0,3})(I[XV]|V?I{0,3})$')

def tighten_quote_spacing(text: str) -> str:
    """Drop padding inside quotes/brackets ('" Hello "' -> '"Hello"') without gluing a quote to the next word.
    A quote opens only at start/after space or a bracket, and closes only before space/punctuation/end."""
    text = re.sub(r'([\(\[\{])\s+', r'\1', text)
    text = re.sub(r'\s+([\)\]\}])', r'\1', text)
    # Closing first, or the closing quote of '" Hello " she' is mistaken for an opening one
    text = re.sub(r'(?<=[\w.,!?…])\s+(' + QUOTE_CLOSE + r')(?=[\s.,!?;:…\)\]\}]|$)', r'\1', text)
    text = re.sub(r'(^|[\s\(\[\{])(' + QUOTE_OPEN + r')\s+(?=\w)', r'\1\2', text)
    return text

DECADE_WORDS = {
    '0': 'hundreds', '1': 'tens', '2': 'twenties', '3': 'thirties', '4': 'forties',
    '5': 'fifties', '6': 'sixties', '7': 'seventies', '8': 'eighties', '9': 'nineties'
}

def say_year(year: str, plural: str = "") -> str:
    """English year form left for the number engine: 1990 -> '19 90', 1900 -> '19 hundred',
    1905 -> '19 oh 5', 1990s -> '19 nineties'. 2000-2009 stay whole ('two thousand and five')."""
    head, tail = year[:2], year[2:]
    if plural and tail.endswith('0'):
        return f"{head} {DECADE_WORDS[tail[0]]}" if tail != '00' or head != '20' else f"{year}{plural}"
    if head == '20' and tail[0] == '0':
        return f"{year}{plural}"
    if tail == '00':
        return f"{head} hundred{plural}"
    if tail[0] == '0':
        return f"{head} oh {tail[1]}{plural}"
    return f"{head} {tail}{plural}"

ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
# Valid numerals that are really words/abbreviations when written in caps
ROMAN_SKIP = {'MIX', 'CLI', 'CIV'}
ROMAN_TOKEN = re.compile(r'(?<![A-Za-z0-9])([MDCLXVImdclxvi]{2,})(?![A-Za-z0-9])')
ROMAN_SINGLE = re.compile(r'\b(Chapter|Part|Book|Volume|Vol\.|Act|Scene|Episode|Arc|Season|Phase|Stage)\s+([IVX])(?![A-Za-z0-9\'’\-])')
ROMAN_OPENERS = '([{"\'“‘'
ROMAN_CLOSERS = '.,!?;:…)]}"\'”’'

def roman_to_int(word: str) -> int:
    total = 0
    for i, ch in enumerate(word):
        val = ROMAN_VALUES[ch]
        total += -val if i + 1 < len(word) and ROMAN_VALUES[word[i + 1]] > val else val
    return total

def read_roman_numerals(text: str) -> str:
    """espeak reads I/V/X tokens as 'roman twelve' even when glued (XX-kun) or lowercase (xii).
    Standalone uppercase numerals -> number words; any other I/V/X token is spelled letter by letter."""
    def standalone(s: str, start: int, end: int) -> bool:
        if start > 0 and not (s[start - 1].isspace() or s[start - 1] in ROMAN_OPENERS):
            return False
        after = s[end:end + 2]
        if not after or after[0].isspace():
            return True
        # Trailing sentence punctuation/closing quote is fine; "XX's" or "XX.5" is glue
        return after[0] in ROMAN_CLOSERS and (len(after) < 2 or not after[1].isalnum())

    def replace(match):
        word = match.group(1)
        upper = word.upper()
        if not ROMAN_NUMERAL.match(upper):
            return word
        only_ivx = not re.search(r'[LCDM]', upper)
        if word.isupper() and standalone(match.string, match.start(1), match.end(1)):
            # Two-letter L/C/D/M forms (CD, DC, MD, XL, CV) are abbreviations; espeak spells them fine
            if only_ivx or (len(word) >= 3 and word not in ROMAN_SKIP):
                return num2words(roman_to_int(upper))
            return word
        # Glued or lowercase: block espeak's roman reading by spelling the letters (X-X-kun)
        return '-'.join(upper) if only_ivx else word

    text = ROMAN_SINGLE.sub(lambda m: f"{m.group(1)} {num2words(ROMAN_VALUES[m.group(2)])}", text)
    return ROMAN_TOKEN.sub(replace, text)

def collapse_letter_runs(match):
    word = match.group(0)
    # Keep Roman numerals (Chapter III, Henry VIII) and www; elongations collapse (Ahhhh -> Ahh)
    if ROMAN_NUMERAL.match(word) or word.lower() == 'www':
        return word
    return re.sub(r"([A-Za-z])\1{2,}", r"\1\1", word)

ONSET_VOWEL_IPA = {
    'eɪ': 'ay', 'aɪ': 'eye', 'aʊ': 'ow', 'ɔɪ': 'oy', 'oʊ': 'oh', 'əʊ': 'oh',
    'æ': 'ah', 'ɑ': 'ah', 'a': 'ah', 'ə': 'uh', 'ʌ': 'uh', 'ɐ': 'uh', 'ᵻ': 'ih',
    'ɛ': 'eh', 'e': 'eh', 'ɪ': 'ih', 'i': 'ee', 'u': 'oo', 'ʊ': 'oo',
    'ɔ': 'aw', 'ɒ': 'aw', 'o': 'oh', 'ɜ': 'er', 'ɚ': 'er'
}
ONSET_VOWEL_TEXT = {'a': 'ah', 'e': 'eh', 'i': 'ih', 'o': 'oh', 'u': 'uh'}
ONSET_CONSONANT_SPELL = {'q': 'kw', 'x': 'z'}

def onset_vowel_sound(letter: str, word: str, clean_ipa: str = "") -> str:
    """Single consonant + the word's own first vowel (p-pajama -> 'puh', n-nukimizu -> 'noo').
    Returns "" for consonant clusters (pl-, st-) so the cluster map keeps handling them."""
    if letter in 'aeiou' or len(word) < 2 or word[1] not in 'aeiou':
        return ""
    vowel = ""
    for i, ch in enumerate(clean_ipa):
        if ch in ONSET_VOWEL_IPA:
            vowel = ONSET_VOWEL_IPA.get(clean_ipa[i:i + 2]) or ONSET_VOWEL_IPA[ch]
            break
    if not vowel:
        vowel = ONSET_VOWEL_TEXT[word[1]]
    return ONSET_CONSONANT_SPELL.get(letter, letter) + vowel

def process_jp_stutter(match):
    prefixes = [p for p in match.group(1).split('-') if p]
    name = match.group(2)
    honorific = match.group(3)

    # Only a real stutter when every prefix letter is the name's first letter (T-T-Tanaka, not A-Tanaka)
    if not all(p.lower() == name[0].lower() for p in prefixes):
        return match.group(0)

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

        sounds = [phonetic_prefix[0].upper() + phonetic_prefix[1:] if p.isupper() else phonetic_prefix.lower()
                  for p in prefixes]
        return f"{' '.join(sounds)} {name}-{honorific}"
        
    return match.group(0)

# ==========================================
# 3. MASTER NORMALIZATION ENGINE
# ==========================================

def fix_broken_words(text: str) -> str:
    """Unified engine that fixes spaces, protects exact words, and processes stutters."""
    if not text:
        return text

    # Rejoin split contractions ("don ' t", "it' s") only; a general letter-quote-letter join glued dialogue ("said 'yes'")
    text = re.sub(r"([a-zA-Z])\s*(['’])\s*(s|t|re|ve|ll|d|m)\b", r'\1\2\3', text, flags=re.IGNORECASE)
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

    # Rejoin spaced-out letters ("H e l l o") only for runs of 3+; pairs like "I a" / "a b" are real words
    text = re.sub(r'(?<![^\s"\'\(\[])(?:[a-zA-Z]\s+){2,}[a-zA-Z](?![a-zA-Z0-9])',
                  lambda m: re.sub(r'\s+', '', m.group(0)), text)

    for pattern, replacement in EXACT_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    jp_pattern = r"\b((?:[A-Za-z]-)+)([A-Za-z]+)-(san|chan|kun|sama|dono|senpai|sensei|dano)\b"
    text = re.sub(jp_pattern, process_jp_stutter, text, flags=re.IGNORECASE)

    stutter_re = re.compile(r'(?<![a-zA-Z])([\'"“‘\[\(\{]*)((?:[a-zA-Z]{1,3}[-—–]+)+)([a-zA-Z]+(?:\'[a-zA-Z]+)?)')

    def resolve_stutter(match):
        pre_punct = match.group(1)
        raw_prefixes = match.group(2)
        remainder_of_word = match.group(3)
        rem_low = remainder_of_word.lower()
        
        prefixes = [p for p in re.split(r'[-—–]+', raw_prefixes) if p]
        
        # 1. Prefix match validation; if a leading word ate the run ("Wha--N-N-Nukumizu"), retry after it.
        # An em dash ("--") before single-hyphen stutters is a word break too ("so--s-s-sorry").
        seps = re.findall(r'[-—–]+', raw_prefixes)
        dash_break = len(seps) > 1 and len(seps[0]) > 1 and all(len(s) == 1 for s in seps[1:])
        if dash_break or not all(rem_low.startswith(p.lower()) for p in prefixes):
            head = re.match(r'[a-zA-Z]{1,3}[-—–]+', raw_prefixes).group(0)
            tail = raw_prefixes[len(head):] + remainder_of_word
            if len(prefixes) > 1:
                fixed_tail = stutter_re.sub(resolve_stutter, tail)
                if fixed_tail != tail:
                    return f"{pre_punct}{head}{'' if fixed_tail.startswith(' ') else ' '}{fixed_tail.lstrip(' ')}"
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
        clean_ipa = ""
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
            
            onset_sound = onset_vowel_sound(lookup, rem_low, clean_ipa) if len(lookup) == 1 else ""

            mapped_sound = None
            if smart_sound:
                mapped_sound = smart_sound
            elif onset_sound:
                mapped_sound = onset_sound
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
        # Detach a leading dash/em dash ("--n-n-nukimizu") so it stays a pause, not part of the word
        lead = " " if not pre_punct and match.start() > 0 and match.string[match.start() - 1] == '-' else ""
        return f"{lead}{pre_punct}{' '.join(result)}"

    text = stutter_re.sub(resolve_stutter, text)
    text = re.sub(r"[A-Za-z]+", collapse_letter_runs, text)

    for pattern, phonetic_replacement in INTERJECTION_MAP.items():
        text = re.sub(r'\b' + pattern + r'\b', phonetic_replacement, text, flags=re.IGNORECASE)

    text = tighten_quote_spacing(text)
    
    return re.sub(r'\s+', ' ', text).strip()


JP_DIGIT_MAP = {
    '0': 'ゼロ', '1': 'イチ', '2': 'ニ', '3': 'サン', '4': 'ヨン',
    '5': 'ゴ', '6': 'ロク', '7': 'ナナ', '8': 'ハチ', '9': 'キュウ'
}

EN_DIGIT_MAP = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", 
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
}

KANJI_DIGITS = {
    '0': '〇', '1': '一', '2': '二', '3': '三', '4': '四',
    '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'
}

NUMBER_PATTERN = re.compile(
    r'(?<![a-zA-Z0-9,\.])'
    r'('
    r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
    r')'
    r'(?:(st|nd|rd|th)(?![a-zA-Z0-9])|(?![0-9]|,[0-9]|\.[0-9]|[a-zA-Z]))',
    re.IGNORECASE
)


def fix_special_formats(text: str, lang: str = "en") -> str:
    """Handles edge cases like time, dates, phone numbers, currency, and paper sizes."""
    if not text:
        return text

    text = tighten_quote_spacing(text)

    # Hyphenated capital forces Kokoro G2P to pronounce letter name /eI/ without pause
    text = re.sub(r'\b[Aa](\d+)\b', r'A-\1', text)

    # Universal currency symbol handling for Japanese Yen
    text = re.sub(r'¥\s*(\d+(?:,\d{3})*(?:\.\d+)?)', r'\1円', text)

    # Context detection for phone numbers and hyphenated digit sequences
    has_kana = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text))
    is_jp = lang.startswith('ja') or has_kana

    def replace_hyphenated(match):
        raw_match = match.group(0)
        # Check local boundary context
        s_idx = match.start()
        e_idx = match.end()
        left_c = ""
        for c in reversed(text[:s_idx]):
            if c.strip() and not re.match(r'[0-9.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–:;/\\]', c):
                left_c = c
                break
        right_c = ""
        for c in text[e_idx:]:
            if c.strip() and not re.match(r'[0-9.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–:;/\\]', c):
                right_c = c
                break
        local_jp = is_jp or bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', left_c + right_c))
        parts = raw_match.split('-')
        # Ranges (pages 12-15, 1990-1995, 1990-95) read "A to B"; phone-shaped digits keep digit-by-digit
        if len(parts) == 2 and not any(p.startswith('0') for p in parts):
            a, b = parts
            is_year = lambda y: re.fullmatch(r'1[5-9]\d{2}|20\d{2}', y) is not None
            year_full = len(a) == len(b) == 4 and is_year(a) and is_year(b) and int(a) < int(b)
            year_short = len(a) == 4 and len(b) == 2 and is_year(a) and int(b) > int(a[2:])
            plain_up = len(a) == len(b) < 4 and int(a) < int(b)
            if local_jp and (year_full or year_short or plain_up):
                return f"{a}\u304B\u3089{b}"
            if year_full or year_short:
                return f"{say_year(a)} to {say_year(b) if len(b) == 4 else b}"
            if plain_up:
                return f"{a} to {b}"
        if local_jp:
            kana_parts = ["".join(JP_DIGIT_MAP.get(d, d) for d in p) for p in parts]
            return "、".join(kana_parts)
        else:
            word_parts = [" ".join(EN_DIGIT_MAP.get(d, d) for d in p) for p in parts]
            return ", ".join(word_parts)

    text = re.sub(r'(?<![a-zA-Z0-9])\d{2,4}(?:-\d{2,4}){1,2}(?![a-zA-Z0-9])', replace_hyphenated, text)

    # Currency for US Dollars
    def split_currency(match):
        dollars = match.group(1)
        cents = match.group(2)
        s_idx = match.start()
        left_c = ""
        for c in reversed(text[:s_idx]):
            if c.strip() and not re.match(r'[0-9.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–:;/\\]', c):
                left_c = c
                break
        if is_jp or bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', left_c)):
            return f"{dollars}ドル"
        # $1 -> dollar, $1.5 -> 50 cents, $0.99 -> just cents
        cents = (cents or "").ljust(2, '0')[:2]
        whole = int(dollars.replace(',', '') or 0)
        dollar_part = f"{dollars} {'dollar' if whole == 1 else 'dollars'}"
        if int(cents) > 0:
            cent_part = f"{int(cents)} {'cent' if int(cents) == 1 else 'cents'}"
            return cent_part if whole == 0 else f"{dollar_part} and {cent_part}"
        return dollar_part

    text = re.sub(r'\$([0-9,]+)(?:\.(\d+))?', split_currency, text)

    # Dot-separated time with AM/PM (e.g. 3.43 P.M. -> 3:43 pm) before decimal engine fires
    text = re.sub(r'\b(\d{1,2})\.(\d{2})\s*(?i:a\.?m\.?)(?=\s|[.,!?]|$)', r'\1:\2 am', text)
    text = re.sub(r'\b(\d{1,2})\.(\d{2})\s*(?i:p\.?m\.?)(?=\s|[.,!?]|$)', r'\1:\2 pm', text)

    # Normalize standalone time markers to lowercase
    text = re.sub(r'\b(\d{1,2}):00\b', r'\1', text)
    text = re.sub(r'(\d)\s*(?i:a\.?m\.?)(?=\s|[.,!?]|$)', r'\1 am', text)
    text = re.sub(r'(\d)\s*(?i:p\.?m\.?)(?=\s|[.,!?]|$)', r'\1 pm', text)

    if lang.startswith('en'):
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
            return f"{prefix1} {prefix2}{say_year(year, plural)}"
            
        text = year_pattern.sub(split_year, text)
        text = read_roman_numerals(text)

    return text


def auto_translate_numbers(text: str, lang: str = "en") -> str:
    """Converts numbers to words dynamically, supporting ordinals, decimals, and CJK boundaries."""
    if not text:
        return text

    has_kana_globally = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text))

    def match_to_words(match):
        raw_number = match.group(1)
        suffix = match.group(2)
        raw_string = match.group(0)
        start_idx = match.start()
        end_idx = match.end()

        # Look left skipping punctuation, whitespace, and digits
        left_char = ""
        for char in reversed(text[:start_idx]):
            if char.strip() and not re.match(r'[0-9.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–:;/\\]', char):
                left_char = char
                break

        # Look right skipping punctuation, whitespace, and digits
        right_char = ""
        for char in text[end_idx:]:
            if char.strip() and not re.match(r'[0-9.,!?"\'\(\)\[\]\{\}\-\_“”‘’…—–:;/\\]', char):
                right_char = char
                break

        ja_kana_regex = r'[\u3040-\u309F\u30A0-\u30FF]'
        cjk_regex = r'[\u4E00-\u9FFF]'
        latin_regex = r'[a-zA-Z]'

        is_left_kana = bool(re.match(ja_kana_regex, left_char)) if left_char else False
        is_right_kana = bool(re.match(ja_kana_regex, right_char)) if right_char else False
        is_left_cjk = bool(re.match(cjk_regex, left_char)) if left_char else False
        is_right_cjk = bool(re.match(cjk_regex, right_char)) if right_char else False
        is_left_latin = bool(re.match(latin_regex, left_char)) if left_char else False
        is_right_latin = bool(re.match(latin_regex, right_char)) if right_char else False

        target_lang = 'en'
        if lang.startswith('ja'):
            target_lang = 'ja'
        elif is_left_kana or is_right_kana:
            target_lang = 'ja'
        elif (is_left_cjk or is_right_cjk) and (has_kana_globally or not (is_left_latin or is_right_latin)):
            target_lang = 'ja'
        elif has_kana_globally and not (is_left_latin or is_right_latin):
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

        try:
            if target_lang == 'ja':
                if '.' in raw_number:
                    parts = raw_number.replace(',', '').split('.', 1)
                    whole_str, dec_str = parts[0], parts[1]
                    whole_val = int(whole_str) if whole_str else 0
                    whole_kanji = num2words(whole_val, lang='ja') if whole_val != 0 else '〇'
                    dec_kanji = "".join(KANJI_DIGITS.get(d, d) for d in dec_str)
                    return f"{whole_kanji}点{dec_kanji}"

                clean_number = int(raw_number.replace(',', ''))
                if suffix:
                    return num2words(clean_number, lang='ja', to='ordinal')
                return num2words(clean_number, lang='ja')

            # Non-Japanese languages
            if '.' in raw_number:
                clean_float = float(raw_number.replace(',', ''))
                return num2words(clean_float, lang=target_lang)

            clean_number = int(raw_number.replace(',', ''))
            if suffix:
                return num2words(clean_number, lang=target_lang, to='ordinal')
            return num2words(clean_number, lang=target_lang)

        except Exception:
            return raw_string

    return NUMBER_PATTERN.sub(match_to_words, text)


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
        'K': 'ケー', 'L': 'エル', 'M': 'エム', 'N': 'エヌ', 'O': 'オー', 
        'P': 'ピー', 'Q': 'キュー', 'R': 'アール', 'S': 'エス', 'T': 'ティー', 
        'U': 'ユー', 'V': 'ブイ', 'W': 'ダブリュー', 'X': 'エックス', 'Y': 'ワイ', 'Z': 'ゼット',
        'a': 'エー', 'b': 'ビー', 'c': 'シー', 'd': 'ディー', 'e': 'イー', 
        'f': 'エフ', 'g': 'ジー', 'h': 'エイチ', 'i': 'アイ', 'j': 'ジェー', 
        'k': 'ケー', 'l': 'エル', 'm': 'エム', 'n': 'エヌ', 'o': 'オー', 
        'p': 'ピー', 'q': 'キュー', 'r': 'アール', 's': 'エス', 't': 'ティー', 
        'u': 'ユー', 'v': 'ブイ', 'w': 'ダブリュー', 'x': 'エックス', 'y': 'ワイ', 'z': 'ゼット',
        'Ａ': 'エー', 'Ｂ': 'ビー', 'Ｃ': 'シー', 'Ｄ': 'ディー', 'Ｅ': 'イー', 
        'Ｆ': 'エフ', 'Ｇ': 'ジー', 'Ｈ': 'エイチ', 'Ｉ': 'アイ', 'Ｊ': 'ジェー', 
        'Ｋ': 'ケー', 'Ｌ': 'エル', 'Ｍ': 'エム', 'Ｎ': 'エヌ', 'Ｏ': 'オー', 
        'Ｐ': 'ピー', 'Ｑ': 'キュー', 'Ｒ': 'アール', 'Ｓ': 'エス', 'Ｔ': 'ティー', 
        'Ｕ': 'ユー', 'Ｖ': 'ブイ', 'Ｗ': 'ダブリュー', 'Ｘ': 'エックス', 'Ｙ': 'ワイ', 'Ｚ': 'ゼット',
        'ａ': 'エー', 'ｂ': 'ビー', 'ｃ': 'シー', 'ｄ': 'ディー', 'ｅ': 'イー', 
        'ｆ': 'エフ', 'ｇ': 'ジー', 'ｈ': 'エイチ', 'ｉ': 'アイ', 'ｊ': 'ジェー', 
        'ｋ': 'ケー', 'ｌ': 'エル', 'ｍ': 'エム', 'ｎ': 'エヌ', 'ｏ': 'オー', 
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
        
        clean_item = normalize_unicode_quotes(str(item)).strip()
        if not clean_item:
            continue
        escaped_item = re.escape(clean_item)
        
        start_bound = r'(?<!\w)' if re.match(r'\w', clean_item[0]) else r''
        end_bound = r'(?!\w)' if re.match(r'\w', clean_item[-1]) else r''
        
        pat = f"{start_bound}{escaped_item}{end_bound}"
        # Preserve separation when an ignored symbol sits between two words.
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)

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
                start_bound = r'(?<!\w)' if re.match(r'\w', clean_orig[0]) else r''
                end_bound = r'(?!\w)' if re.match(r'\w', clean_orig[-1]) else r''
                pat = f"{start_bound}{escaped_orig}{end_bound}"
            else:
                pat = escaped_orig
                
            text = re.sub(pat, str(rep), text, flags=flags)

    text = fix_broken_words(text)
    text = fix_special_formats(text, lang)
    text = auto_translate_numbers(text, lang)
    text = protect_japanese_mixed_latin(text, lang)

    return re.sub(r'\s+', ' ', text).strip()
