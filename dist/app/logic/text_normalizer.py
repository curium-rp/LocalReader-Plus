import re
from typing import List, Dict, Any
from num2words import num2words

def fix_broken_words(text: str) -> str:
    """Fixes PDF artifacts like ligatures, ghost spaces, and mid-word hyphens."""
    # 0. Ligatures
    ligatures = {'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl', '\ufb05': 'ft', '\ufb06': 'st', '\u00a0': ' ', '\u2013': '-', '\u2014': '--'}
    for char, rep in ligatures.items(): text = text.replace(char, rep)

    # 1. De-hyphenation
    text = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', text)
    
    # 2. Ghost spaces in common words
    common = [(r'\bo\s+ff\b', 'off'), (r'\bo\s+f\b', 'of'), (r'\ba\s+nd\b', 'and'), (r'\bt\s+he\b', 'the'), (r'\bi\s+n\b', 'in'), (r'\bi\s+t\b', 'it'), (r'\bi\s+s\b', 'is'), (r'\bt\s+o\b', 'to'), (r'\bs\s+t\b', 'st')]
    for pat, rep in common: text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    # 3. Recursive single letter join (e.g. "W o r d" -> "Word")
    old = ""
    while old != text:
        old = text
        text = re.sub(r'(?:^|(?<=\s))([a-zA-Z])\s+([a-zA-Z])(?=\s|$)', r'\1\2', text)
    
    # 4. Cleanup punctuation spaces
    text = re.sub(r'([\"\'\(\[\{\u201c\u2018\u201d\u2019])\s+', r'\1', text)
    text = re.sub(r'\s+([\"\'\)\\\}\]\u201c\u2018\u201d\u2019])', r'\1', text)
    text = re.sub(r'(?<=[\"\'\u201c\u2018\u201d\u2019])\s+', '', text)
    text = re.sub(r'\s+(?=[\"\'\u201c\u2018\u201d\u2019])', '', text)
    
    return re.sub(r'\s+', ' ', text).strip()


def fix_special_formats(text: str) -> str:
    """Handles edge cases like time, dates, phone numbers, and currency."""
    if not text:
        return text

    # 1. QUOTES & BRACKETS: Remove awkward spacing
    text = re.sub(r'([\"\'\(\[\{\u201c\u2018])\s+', r'\1', text)
    text = re.sub(r'\s+([\"\'\)\}\]\u201d\u2019])', r'\1', text)

    # 2. SMART CURRENCY: Handle dollars and optional cents
    def split_currency(match):
        dollars = match.group(1)
        cents = match.group(2)
        # Only read cents if they exist and are greater than 00
        if cents and int(cents) > 0:
            return f"{dollars} dollars and {cents} cents"
        return f"{dollars} dollars"
    
    # Matches $3443, $3443.89, and $3443.00
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
    # Added 'through', 'between', and 'and' to catch ranges like "2022 through 2026"
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


def auto_translate_numbers(text: str) -> str:
    """Converts comma-formatted numbers to words via num2words."""
    if not text:
        return text

    def match_to_words(match):
        raw_string = match.group(0)
        clean_number = int(raw_string.replace(',', ''))
        return num2words(clean_number)

    return re.sub(r'\b\d{1,3}(?:,\d{3})+\b', match_to_words, text)


def apply_custom_pronunciations(text: str, rules: List[Dict[str, Any]], ignore_list: List[str] = []) -> str:
    """Applies the custom user rules from the UI sidebar after automatic normalization."""
    # Run automatic text fixes first
    text = fix_broken_words(text)
    text = auto_translate_numbers(text)

    if not rules and not ignore_list:
        return text

    # Apply ignore list
    for item in ignore_list:
        if item: 
            text = re.sub(re.escape(item), "", text, flags=re.IGNORECASE)

    # Apply pronunciation rules
    for rule in rules:
        orig, rep = rule.get("original", ""), rule.get("replacement", "")
        if not orig: 
            continue
        
        pat = re.escape(orig)
        if rule.get("word_boundary"): 
            pat = f"\\b{pat}\\b"
            
        text = re.sub(pat, rep, text, flags=0 if rule.get("match_case") else re.IGNORECASE)
            
    return text


def inject_pauses(text: str, pause_settings: Dict[str, int]) -> str:
    """Placeholder for future TTS engines that support SSML."""
    return text