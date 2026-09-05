import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

def has_onnxruntime_gpu() -> bool:
    capi = Path(sys.prefix) / "Lib" / "site-packages" / "onnxruntime" / "capi"
    return (capi / "onnxruntime_providers_cuda.dll").exists() or (
        capi / "libonnxruntime_providers_cuda.so"
    ).exists()

_ISO639_2_TO_1 = {
    "aar": "aa", "abk": "ab", "afr": "af", "aka": "ak", "alb": "sq", "amh": "am",
    "ara": "ar", "arg": "an", "arm": "hy", "asm": "as", "ava": "av", "ave": "ae",
    "aym": "ay", "aze": "az", "bak": "ba", "bam": "bm", "baq": "eu", "bel": "be",
    "ben": "bn", "bih": "bh", "bis": "bi", "bod": "bo", "bos": "bs", "bre": "br",
    "bul": "bg", "bur": "my", "cat": "ca", "ces": "cs", "cha": "ch", "che": "ce",
    "chi": "zh", "chu": "cu", "chv": "cv", "cor": "kw", "cos": "co", "cre": "cr",
    "cym": "cy", "cze": "cs", "dan": "da", "deu": "de", "div": "dv", "dut": "nl",
    "dzo": "dz", "ell": "el", "eng": "en", "epo": "eo", "est": "et", "eus": "eu",
    "ewe": "ee", "fao": "fo", "fas": "fa", "fij": "fj", "fin": "fi", "fra": "fr",
    "fre": "fr", "fry": "fy", "ful": "ff", "geo": "ka", "ger": "de", "gla": "gd",
    "gle": "ga", "glg": "gl", "glv": "gv", "gre": "el", "grn": "gn", "guj": "gu",
    "hat": "ht", "hau": "ha", "heb": "he", "her": "hz", "hin": "hi", "hmo": "ho",
    "hrv": "hr", "hun": "hu", "hye": "hy", "ibo": "ig", "ice": "is", "ido": "io",
    "iii": "ii", "iku": "iu", "ile": "ie", "ina": "ia", "ind": "id", "ipk": "ik",
    "isl": "is", "ita": "it", "jav": "jv", "jpn": "ja", "kal": "kl", "kan": "kn",
    "kas": "ks", "kat": "ka", "kau": "kr", "kaz": "kk", "khm": "km", "kik": "ki",
    "kin": "rw", "kir": "ky", "kom": "kv", "kon": "kg", "kor": "ko", "kua": "kj",
    "kur": "ku", "lao": "lo", "lat": "la", "lav": "lv", "lim": "li", "lin": "ln",
    "lit": "lt", "ltz": "lb", "lub": "lu", "lug": "lg", "mac": "mk", "mah": "mh",
    "mal": "ml", "mao": "mi", "mar": "mr", "may": "ms", "mkd": "mk", "mlg": "mg",
    "mlt": "mt", "mon": "mn", "mri": "mi", "msa": "ms", "mya": "my", "nau": "na",
    "nav": "nv", "nbl": "nr", "nde": "nd", "ndo": "ng", "nep": "ne", "nld": "nl",
    "nno": "nn", "nob": "nb", "nor": "no", "nya": "ny", "oci": "oc", "oji": "oj",
    "ori": "or", "orm": "om", "oss": "os", "pan": "pa", "per": "fa", "pli": "pi",
    "pol": "pl", "por": "pt", "pus": "ps", "que": "qu", "roh": "rm", "ron": "ro",
    "rum": "ro", "run": "rn", "rus": "ru", "sag": "sg", "san": "sa", "sin": "si",
    "slk": "sk", "slo": "sk", "slv": "sl", "sme": "se", "smo": "sm", "sna": "sn",
    "snd": "sd", "som": "so", "sot": "st", "spa": "es", "srd": "sc", "srp": "sr",
    "ssw": "ss", "sun": "su", "swa": "sw", "swe": "sv", "tah": "ty", "tam": "ta",
    "tat": "tt", "tel": "te", "tgk": "tg", "tgl": "tl", "tha": "th", "tib": "bo",
    "tir": "ti", "ton": "to", "tsn": "tn", "tso": "ts", "tuk": "tk", "tur": "tr",
    "twi": "tw", "uig": "ug", "ukr": "uk", "urd": "ur", "uzb": "uz", "ven": "ve",
    "vie": "vi", "vol": "vo", "wel": "cy", "wln": "wa", "wol": "wo", "xho": "xh",
    "yid": "yi", "yor": "yo", "zha": "za", "zho": "zh", "zul": "zu",
    "cmn": "zh", "yue": "zh", "nan": "zh", "fil": "fil",
}

_INVALID_LANGS = {"", "und", "zxx", "mis", "mul", "qaa", "null", "none"}
_ROOT_LANG_RE = re.compile(
    r"<(?:html|body)\b[^>]*\s(?:xml:)?lang\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_XMP_LANG_RE = re.compile(
    r"(?:dc:language|pdf:Lang|pdfx:Language)\s*>\s*([^<]+)",
    re.IGNORECASE,
)


def normalize_bcp47(value: Any) -> Optional[str]:
    """Collapse OPF/PDF language tags to IETF BCP 47 for CSS hyphens."""
    if value is None:
        return None
    raw = str(value).strip().replace("_", "-")
    if not raw:
        return None
    raw = raw.split(",")[0].strip()
    raw = raw.strip(".'\"()[]{}/\\ ")
    if not raw:
        return None

    parts = [part for part in raw.split("-") if part]
    if not parts:
        return None

    primary = parts[0].lower()
    if len(primary) == 3:
        primary = _ISO639_2_TO_1.get(primary, primary)
    if primary in _INVALID_LANGS or not re.fullmatch(r"[a-z]{2,3}|fil", primary):
        return None

    rest = [part for part in parts[1:] if part]
    script = next((part for part in rest if len(part) == 4 and part.isalpha()), "")
    region = next((part for part in rest if len(part) == 2 and part.isalpha()), "")
    if not region:
        region_num = next((part for part in rest if len(part) == 3 and part.isdigit()), "")
        region = region_num

    script_l = script.lower()
    region_u = region.upper()

    if primary == "zh":
        if script_l == "hant" or region_u in {"TW", "HK", "MO"}:
            return "zh-TW"
        if script_l == "hans" or region_u in {"CN", "SG"}:
            return "zh-CN"
        return "zh"

    if primary == "ja":
        return "ja"

    return primary


def language_from_epub_book(book: Any) -> Optional[str]:
    try:
        metas = book.get_metadata("DC", "language") or []
    except Exception:
        metas = []
    for item in metas:
        raw = item[0] if isinstance(item, (tuple, list)) and item else item
        tag = normalize_bcp47(raw)
        if tag:
            return tag
    return None


def language_from_pdf_doc(doc: Any) -> Optional[str]:
    try:
        xref = doc.pdf_catalog()
        kind, value = doc.xref_get_key(xref, "Lang")
        if kind and kind != "null" and value and str(value).lower() != "null":
            tag = normalize_bcp47(str(value).lstrip("/"))
            if tag:
                return tag
    except Exception:
        pass

    try:
        meta = doc.metadata or {}
    except Exception:
        meta = {}
    for key in ("language", "lang", "Language", "Lang"):
        tag = normalize_bcp47(meta.get(key) if isinstance(meta, dict) else None)
        if tag:
            return tag

    try:
        xmp = doc.get_xml_metadata() or ""
        match = _XMP_LANG_RE.search(xmp)
        if match:
            tag = normalize_bcp47(match.group(1))
            if tag:
                return tag
    except Exception:
        pass
    return None


def language_from_html_markup(markup: str) -> Optional[str]:
    if not markup:
        return None
    match = _ROOT_LANG_RE.search(markup)
    if not match:
        return None
    return normalize_bcp47(match.group(1))


def language_from_pages(pages: Any) -> Optional[str]:
    if not pages:
        return None
    for page in list(pages)[:8]:
        tag = language_from_html_markup(str(page or ""))
        if tag:
            return tag
    return None


def language_from_text_heuristic(text: str) -> Optional[str]:
    """Script profiling only — never guess among Latin languages."""
    if not text:
        return None
    sample = text[:8000]
    if len(sample.strip()) < 40:
        return None

    kana = hangul = han = cyrillic = arabic = latin = 0
    for char in sample:
        code = ord(char)
        if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
            kana += 1
        elif 0xAC00 <= code <= 0xD7AF:
            hangul += 1
        elif 0x4E00 <= code <= 0x9FFF:
            han += 1
        elif 0x0400 <= code <= 0x04FF:
            cyrillic += 1
        elif 0x0600 <= code <= 0x06FF:
            arabic += 1
        elif char.isalpha() and code < 0x024F:
            latin += 1

    if kana >= 8:
        return "ja"
    if hangul >= 16:
        return "ko"
    if han >= 16 and kana == 0:
        return "zh"
    if cyrillic >= 24 and cyrillic > latin:
        return "ru"
    if arabic >= 24 and arabic > latin:
        return "ar"
    return None


def safe_save_json(path: Path, data: Any):
    """Atomic write to prevent corruption"""
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(data, f)
    temp_path.replace(path)


def safe_init_json(path: Path, default_data: Any):
    """Initialize JSON file if it doesn't exist"""
    if not path.exists():
        with open(path, "w") as f:
            json.dump(default_data, f)


def get_language_from_voice(voice: str) -> str:
    """
    Detect language from voice ID prefix.
    Returns appropriate language code for Kokoro TTS.
    """
    if voice.startswith(("af_", "am_")):
        return "en-us"
    elif voice.startswith(("bf_", "bm_")):
        return "en-gb"
    elif voice.startswith(("ff_", "fm_")):
        return "fr-fr"
    elif voice.startswith(("ef_", "em_")):
        return "es"
    elif voice.startswith(("zf_", "zm_")):
        return "cmn"
    elif voice.startswith(("if_", "im_")):
        return "it"
    elif voice.startswith(("pf_", "pm_")):
        return "pt-br"
    elif voice.startswith(("jf_", "jm_")):
        return "ja"
    else:
        return "en-us"
