"""Shared TTS text and voice helpers; no router, engine, or app state."""

import html
import re
from typing import List, Tuple

import numpy as np


_RUBY_ANNOTATION_RE = re.compile(r"<(?:rt|rp)\b[^>]*>[\s\S]*?</(?:rt|rp)>", re.I)


_STRUCTURAL_BREAK_RE = re.compile(
    r"<(br|/p|/div|/h[1-6]|hr|/td|/th|/li|/tr)[^>]*>", re.I
)


_VALID_HTML_TAGS_RE = re.compile(
    r"</?(?:n|s|p|div|h[1-6]|span|font|a|b|i|u|em|strong|ins|del|strike|"
    r"sub|sup|mark|small|big|abbr|cite|dfn|q|code|pre|ruby|rt|rp|"
    r"figure|figcaption|blockquote|img|image|svg|picture|hr|br|"
    r"li|ul|ol|table|caption|tr|td|th|tbody|thead|tfoot|"
    r"section|article|aside|nav|main|header|footer|address|dd|dt|summary)"
    r"\b[^>]*>",
    re.I,
)


def is_blend_expression(expr: str) -> bool:
    """Check if a string is a voice blend expression."""
    if not isinstance(expr, str):
        return False
    if "+" in expr:
        return True
    return bool(re.search(r"\w+\(\s*[\d.]+\s*\)", expr))


def parse_blend_expression(expr: str) -> List[Tuple[str, float]]:
    """
    Parse blend string like:
      'af_sky(0.30)+af_heart(0.70)'
      'af_sky(30)+af_heart(70)'
      'af_sky+af_heart'
    Returns list of (voice_name, normalized_weight) where sum of weights is 1.0.
    """
    if not expr or not expr.strip():
        return []

    parts = [p.strip() for p in expr.split("+") if p.strip()]
    raw_slots = []

    for part in parts:
        match = re.match(r"^([\w-]+)(?:\(([\d.]+)\))?$", part)
        if match:
            v_name = match.group(1).strip()
            w_str = match.group(2)
            weight = float(w_str) if w_str is not None else 1.0
            raw_slots.append((v_name, max(0.0, weight)))
        else:
            raw_slots.append((part, 1.0))

    if not raw_slots:
        return []

    total_w = sum(w for _, w in raw_slots)
    if total_w <= 0:
        return [(v, 1.0 / len(raw_slots)) for v, _ in raw_slots]

    normalized = [(v, w / total_w) for v, w in raw_slots]
    return normalized


def get_primary_voice(expr: str) -> str:
    """
    Extract the dominant voice (highest weight) from a blend expression
    to determine language prefix, phonemizer, and fallback behavior.
    """
    slots = parse_blend_expression(expr)
    if not slots:
        return "af_heart"
    return max(slots, key=lambda s: s[1])[0]


def get_english_weight_ratio(expr: str) -> float:
    """
    Calculate the normalized proportion (0.0 - 1.0) of English voices
    (af_*, am_*, bf_*, bm_*) in a blend expression.
    """
    slots = parse_blend_expression(expr)
    if not slots:
        return 0.0
    return sum(
        w for v, w in slots
        if v.startswith(("af_", "am_", "bf_", "bm_"))
    )


def strip_html_for_speech(text: str) -> str:
    """Drop known markup; leftover <...> becomes spoken quotes (dialogue/system)."""
    safe_text = html.unescape(text)
    safe_text = _RUBY_ANNOTATION_RE.sub("", safe_text)
    safe_text = _STRUCTURAL_BREAK_RE.sub(" \n ", safe_text)
    safe_text = _VALID_HTML_TAGS_RE.sub(" ", safe_text)
    return safe_text.replace("<", "“").replace(">", "”")


def create_anti_skip_silence(duration_ms, sample_rate=24000):
    if duration_ms <= 0:
        return np.array([], dtype=np.float32)
    frames = int((duration_ms / 1000.0) * sample_rate)
    return np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)


def sanitize_typography_for_engine(text: str) -> str:
    if not text:
        return text
        
    import re
    import unicodedata

    # ==========================================
    # 🛡️ PHASE 1: UNICODE & INVISIBLE CHARACTERS
    # ==========================================
    # 1. NFKC Normalization: Converts "Full-Width" Asian/English hybrids (Ｈｅｌｌｏ) 
    # and weird ligatures into standard ASCII so Kokoro can read them.
    text = unicodedata.normalize('NFKC', text)
    
    # 🌟 IDEMPOTENCY SHIELD: Protect internal tokens if function is called multiple times
    text = text.replace('<BYP_COM>', '§§BYP_COM§§').replace('<CAP_COM>', '§§CAP_COM§§')
    
    # Destroy raw < and > so they don't crash Kokoro's grapheme parser or interfere with our shields later
    text = text.replace('<', '').replace('>', '')
    
    # Restore internal shields
    text = text.replace('§§BYP_COM§§', '<BYP_COM>').replace('§§CAP_COM§§', '<CAP_COM>')

    # 2. Vaporize Invisible Formatting (Zero-Width Spaces, BOMs, LRM/RLM markers)
    # These characters are invisible but cause Kokoro to output heavy, awkward pauses.
    text = re.sub(r'[\u200B-\u200D\uFEFF\u200E\u200F\u00A0]', ' ', text)

    # 3. The Emoji & Symbol Vaporizer 
    # Kokoro crashes on Emojis. This regex safely strips out high-plane emojis 
    # while perfectly preserving English, Japanese, Chinese, and standard punctuation.
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)

    # ==========================================
    # 🛡️ PHASE 2: TYPOGRAPHY & PUNCTUATION
    # ==========================================
    # 4. Standardize all quote formats
    text = text.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    text = text.replace('«', '"').replace('»', '"').replace('`', "'")
    
    # 5. Asian punctuation to Standard ASCII
    text = text.replace('。', '.').replace('、', ',').replace('！', '!').replace('？', '?')
    text = text.replace('…', '...').replace('．', '.').replace('，', ',')
    # Convert Tildes to Full Stops for not make engine read "Tildes"
    text = text.replace('~', '.').replace('〜', '.').replace('〰', '.')
    # Remove Decorative Symbols, for not make Kokoro read it 
    text = re.sub(r'[・※◎〇●△▲▽▼◇◆□■☆★♥♡❤♪♫♬♀♂†‡∞×]', ' ', text)

    # ==========================================
    # 🛡️ PHASE 3: THE COMMA & NUMBER SHIELDS
    # ==========================================
    # 7. Protect decimals and thousands separators (100,000 / 3.14)
    text = re.sub(r'(?<=\d),(?=\d)', '<NUM_COM>', text)
    text = re.sub(r'(?<=\d)\.(?=\d)', '<NUM_DOT>', text)
    text = re.sub(r'(?<=\d):(?=\d)', '<NUM_COL>', text)
    
    # 8. Group 1: Zero-Pause Bypass (Direct addresses, conversational glue, tags)
    g1_before = r"(?i),\s*(too|sir|ma'am|yeah|then|ever|right|isn't it|is it|do you|don't you|won't you|can you|will you|man|bro|buddy|honey|darling|my lord|child|boy|girl|guys|idiot|fool|no matter what)(?:\b|\?)"
    text = re.sub(g1_before, r'<BYP_COM> \1', text)

    g1_after = r"(?i)\b(oh|ah|me|you|wait|please|no|yes)\s*,"
    text = re.sub(g1_after, r'\1<BYP_COM>', text)

    # 9. Group 2: Micro-Pause Cap (Transitions, starters, modifiers)
    g2_after = r"(?i)\b(again|then|huh|after all|are they|guys|anyway|either|indeed|though|now|later|soon|first|next|finally|suddenly|however|therefore|furthermore|in fact|of course|for example|meanwhile|otherwise|besides|honestly|seriously|luckily|fortunately|sadly|obviously|clearly|actually|basically|well|so|say|listen|look)\s*,"
    text = re.sub(g2_after, r'\1<CAP_COM>', text)
    
    # 10. Fix squished punctuation safely (e.g., "Wait!Stop" -> "Wait! Stop")
    text = re.sub(r'([\.!\?:;,]+)(?=[a-zA-Z0-9])', r'\1 ', text)
    
    # 11. Drop the Number shield (So normalizers can read the numbers correctly)
    text = text.replace('<NUM_COM>', ',').replace('<NUM_DOT>', '.').replace('<NUM_COL>', ':')
    
    # 12. Final cleanup of multiple spaces created by replacements 
    text = re.sub(r'[^\S\r\n]+', ' ', text)
    
    return text.strip()
