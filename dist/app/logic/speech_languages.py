"""The Reader's language handling for speech_core: the inline language switcher,
Japanese/Chinese G2P, and removal of text whose language package is missing."""

import re

from speech_core.tts_helpers import get_english_weight_ratio, get_language_from_voice

from . import language_packs
from .chinese_g2p import cleanse_chinese_text
from .japanese_g2p import pure_japanese_to_romaji
from .language_switcher import smart_polyglot_split

_KANA = re.compile(r'[぀-ヿｦ-ﾟ]+')
_HAN = re.compile(r'[㐀-䶿一-鿿]+')
_CJK_MARKS = re.compile(r'[　-〿＀-･]+')


def drop_unreadable_cjk(text: str, bypassed: set) -> str:
    """Remove Japanese/Chinese letters whose language package is not installed.

    For text that reaches the engine without its own pipeline: the language
    switcher is off (v1.1, a foreign blend) or the voice is another language.
    With the package installed the text is left exactly as before.
    """
    has_kana, has_han = bool(_KANA.search(text)), bool(_HAN.search(text))
    if not (has_kana or has_han):
        return text
    removed = False
    if has_kana and not language_packs.ready('ja'):
        # Kanji in a Japanese sentence are unreadable without the same package.
        text = _HAN.sub(' ', _KANA.sub(' ', text))
        bypassed.add('ja')
        removed, has_han = True, False
    if has_han and not language_packs.ready('zh'):
        text = _HAN.sub(' ', text)
        bypassed.add('zh')
        removed = True
    if not removed:
        return text
    text = re.sub(r'\s+', ' ', _CJK_MARKS.sub(' ', text)).strip()
    # Punctuation that belonged to the removed text closes up to what is left.
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    # Only punctuation left: nothing to say.
    return text if re.search(r'[A-Za-z0-9À-ɏ]', text) else ''


class ReaderLanguages:
    """speech_core language hooks for one model.

    The switcher is off for Kokoro 1.1-zh (a prototype engine: direct synthesis
    only), and for blends under 50% English, which would lose their foreign voice.
    """

    def __init__(self, model_id):
        self.model_id = model_id

    def _switcher_on(self, voice, blended):
        if self.model_id == "kokoro-v1.1":
            return False
        return get_english_weight_ratio(voice) >= 0.50 if blended else True

    def segments(self, text, voice, voice_lang, blended):
        if self._switcher_on(voice, blended):
            return smart_polyglot_split(text, voice, get_language_from_voice)
        return [{"text": text, "voice": voice, "lang": voice_lang, "is_fallback": False}]

    def prepare(self, text, segment, segment_lang, main_lang, selected_voice, bypassed):
        detected = segment["lang"]
        if detected == "ja" or segment_lang.startswith("ja"):
            try:
                return pure_japanese_to_romaji(text), "en-us"
            except ImportError:
                bypassed.add("ja")
                return None
        if detected == "cmn" or segment_lang.startswith(("cmn", "zh")):
            try:
                text = cleanse_chinese_text(text)
            except ImportError:
                bypassed.add("zh")
                return None
            english_main = main_lang.startswith("en") and segment["voice"] == selected_voice
            return text, "en-us" if english_main else segment_lang
        text = drop_unreadable_cjk(text, bypassed)
        return text, "en-us" if main_lang.startswith(("ja", "cmn")) else segment_lang
