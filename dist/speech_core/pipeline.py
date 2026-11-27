"""Text to speech samples: cleanup, pronunciations, punctuation and unit pauses.

Language handling is pluggable. PlainLanguages speaks the text in the voice's
own language; the Reader supplies its switcher and Japanese/Chinese G2P."""

import re
from dataclasses import dataclass, field

import numpy as np
from kokoro_onnx import SAMPLE_RATE

from .request import DEFAULT_BEHAVIORS, SynthesisRequest
from .syllable import graceful_chunk_for_tts
from .text_normalizer import apply_custom_pronunciations, fix_special_formats
from .tts_helpers import (
    create_anti_skip_silence,
    get_language_from_voice,
    is_blend_expression,
    sanitize_typography_for_engine,
    strip_html_for_speech,
)

DEFAULT_VOICES = ("af_heart", "af_maple", "zf_001", "af_bella")
HEAVY_ENDINGS = ("!", "?", "！", "？")
_PUNCTUATION = (",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、")
_SPEAKABLE = re.compile(r"[a-zA-Z0-9　-〿぀-ゟ゠-ヿ＀-ﾟ一-龯㐀-䶿]")
_WORDS = re.compile(r"[a-zA-Z0-9぀-ヿ一-龯]")
_TRAILING_QUOTES = re.compile(r"[\'\"”’」』\s]+$")
_SHIELDS = (("<NUM_COM>", ","), ("<NUM_DOT>", "."), ("<NUM_COL>", ":"), ("<BYP_COM>", ","), ("<CAP_COM>", ","))
# Punctuation, any closing quotes, and a newline form one pause, never two stacked.
_PAUSE_SPLIT = re.compile(r"([,，、\.。\?？!！:：;；]+[ \t]*['\"”’」』]*[ \t]*(?:\n)?|<CAP_COM>|\n+)")
_PAUSE_KEYS = {
    ",": "comma", "，": "comma", "、": "comma", ".": "period", "。": "period",
    "?": "question", "？": "question", "!": "exclamation", "！": "exclamation",
    ":": "colon", "：": "colon", ";": "semicolon", "；": "semicolon",
}


class SpeechUnavailable(RuntimeError):
    """No model is loaded in this process."""


@dataclass
class Speech:
    samples: np.ndarray
    sample_rate: int
    # Languages whose text was left out because their package is not installed.
    bypassed: set = field(default_factory=set)


class PlainLanguages:
    """The whole text as one segment, in the selected voice's language."""

    def segments(self, text, voice, voice_lang, blended):
        return [{"text": text, "voice": voice, "lang": voice_lang, "is_fallback": False}]

    def prepare(self, text, segment, segment_lang, main_lang, selected_voice, bypassed):
        """(text, engine language) for one segment, or None to leave it out."""
        return text, segment_lang


PLAIN = PlainLanguages()


def _unshield(text):
    for token, mark in _SHIELDS:
        text = text.replace(token, mark)
    return text


def _ends_heavy(text):
    tail = _TRAILING_QUOTES.sub("", re.sub(r"<[^>]+>", "", text).strip()).strip()
    return bool(tail) and tail[-1] in HEAVY_ENDINGS


def _silence_pad(ms, sample_rate):
    return np.random.uniform(-1e-4, 1e-4, int(ms / 1000.0 * sample_rate)).astype(np.float32)


def _pause_plan(text, speed, pause_settings, behavior_settings):
    """Split text at punctuation into [{"type": "tts"|"silence", ...}] steps."""
    text = re.sub(r"(?<=\d),(?=\d)", "<NUM_COM>", text.strip())
    text = re.sub(r"(?<=\d)\.(?=\d)", "<NUM_DOT>", text)
    text = re.sub(r"(?<=\d):(?=\d)", "<NUM_COL>", text)
    pieces = _PAUSE_SPLIT.split(text)

    plan, buffer = [], ""
    for i in range(0, len(pieces), 2):
        punctuation = pieces[i + 1] if i + 1 < len(pieces) else ""
        buffer += pieces[i] + punctuation
        has_newline = "\n" in punctuation
        mark = punctuation.replace("\n", "").strip()

        pause_ms = 0
        if mark == "<CAP_COM>":
            # A capped micro-pause: 150 ms at 1.0x, shorter when faster.
            base = int(pause_settings.get("comma", 0))
            pause_ms = min(base, int(150 / speed)) if base > 0 else 0
        elif mark or has_newline:
            core = re.sub(r"[\'\"”’」』\s]", "", mark)
            mark_pause = 0
            if core:
                stops = len(re.findall(r"[\.。\?？!！]", core))
                if stops >= 2:
                    mark_pause = int(pause_settings.get("spam", 0)) * (stops - 1)
                elif core[-1] in _PAUSE_KEYS:
                    mark_pause = int(pause_settings.get(_PAUSE_KEYS[core[-1]], 0))
            if has_newline and not re.search(r"[\?？!！]", core):
                # A period yields to the line break's pause; ? and ! keep their own.
                pause_ms = int(behavior_settings.get("N", DEFAULT_BEHAVIORS["N"]))
            else:
                pause_ms = mark_pause

        if pause_ms > 0:
            if _WORDS.search(buffer):
                plan.append({"type": "tts", "text": _unshield(buffer.strip())})
            plan.append({"type": "silence", "ms": pause_ms})
            buffer = ""

    if buffer.strip() and _WORDS.search(buffer):
        plan.append({"type": "tts", "text": _unshield(buffer.strip())})
    return plan


def _speak_chunks(engine, text, voice, speed, lang, cache):
    samples = []
    for chunk in graceful_chunk_for_tts(text):
        try:
            audio, _ = engine.generate(chunk["text"], voice, speed, lang, cache=cache)
        except Exception as e:
            print(f"Segment failed: {e}")
            continue
        if audio is not None and len(audio.flatten()) > 0:
            samples.append(audio.flatten())
    return samples


def synthesize_with_pauses(engine, text, voice, speed, lang, pause_settings, behavior_settings, cache=True):
    """Samples for text, with silence of the configured length at each punctuation mark."""
    voices = engine.voices()
    if voices and not is_blend_expression(voice) and voice not in voices:
        voice = "af_heart" if "af_heart" in voices else voices[0]

    parts = []
    for step in _pause_plan(text, speed, pause_settings, behavior_settings):
        if step["type"] == "silence":
            parts.append(create_anti_skip_silence(step["ms"], SAMPLE_RATE))
        else:
            parts += _speak_chunks(engine, step["text"], voice, speed, lang, cache)
    parts = [p for p in parts if len(p) > 0]
    if parts:
        return np.concatenate(parts), SAMPLE_RATE
    return create_anti_skip_silence(100, SAMPLE_RATE), SAMPLE_RATE


def _select_voice(request, voices, active_blend):
    if active_blend and is_blend_expression(active_blend):
        return active_blend, True
    if is_blend_expression(request.voice):
        return request.voice, True
    if request.voice in voices:
        return request.voice, False
    return next((v for v in DEFAULT_VOICES if v in voices), voices[0] if voices else "af_heart"), False


def _unit_pauses(behavior_type, behaviors, text):
    """(front ms, end ms) around the unit. Headings, images and scene breaks
    replace the normal end-of-text pause, so it is zeroed for their pauses too."""
    if behavior_type.startswith("H"):
        base = behaviors.get("H", DEFAULT_BEHAVIORS["H"])
        behaviors["N"] = 0
        if base <= 0:
            return 0, 0
        # H1 full, H2 half, then two-thirds per level; 100% before, 30% after, 10 s cap.
        level = {"H1": 0, "H2": 1, "H3": 2}.get(behavior_type, 3)
        size = base / 2.0 / (1.5 ** (level - 1)) if level else base
        return min(int(size), 10000), min(int(size * 0.30), 10000)
    if behavior_type in ("Img", "S"):
        behaviors["N"] = 0
        return 0, int(behaviors.get(behavior_type, DEFAULT_BEHAVIORS[behavior_type]))
    if behavior_type == "N" and not _ends_heavy(text):
        return 0, int(behaviors.get("N", DEFAULT_BEHAVIORS["N"]))
    return 0, 0


def synthesize(engine, request: SynthesisRequest, active_blend="", languages=PLAIN) -> Speech:
    """Speech for one unit of text. A valid `active_blend` overrides request.voice."""
    if not engine.loaded:
        raise SpeechUnavailable("TTS Engine not initialized.")

    text = request.text
    structural_pause_ms = 0
    pause_match = re.search(r"\[PAUSE_(\d+)\]\s*", text)
    if pause_match:
        structural_pause_ms = int(pause_match.group(1))
        text = text.replace(pause_match.group(0), "")
    text = strip_html_for_speech(text)
    text = re.sub(r"\[(?:IMAGE|IMG|VIDEO).*?\]", "", text, flags=re.IGNORECASE)
    original_text = text = sanitize_typography_for_engine(text)

    voices = engine.voices()
    selected_voice, blended = _select_voice(request, voices, active_blend)
    main_lang = get_language_from_voice(selected_voice)

    try:
        rules = [r.model_dump() for r in request.rules] if request.rules else []
        text = apply_custom_pronunciations(text, rules, request.ignore_list, main_lang)
    except Exception:
        text = fix_special_formats(text, main_lang)
    # The typography shield keeps ONNX int32 tensors from crashing on stray symbols.
    text = sanitize_typography_for_engine(text)

    behavior_type = request.behavior_type or "N"
    behaviors = dict(request.behavior_settings or DEFAULT_BEHAVIORS)
    pauses = request.pause_settings or {}
    front_ms, end_ms = _unit_pauses(behavior_type, behaviors, original_text)
    end_ms += structural_pause_ms
    speed = float(request.speed or 1.0)
    bypassed = set()

    # Images and scene breaks are pure silence; their placeholder text is never spoken.
    if behavior_type in ("Img", "S") or not _SPEAKABLE.search(text):
        total = front_ms + end_ms
        return Speech(create_anti_skip_silence(total if total > 0 else 100, SAMPLE_RATE), SAMPLE_RATE, bypassed)

    has_punctuation = any(p in text for p in _PUNCTUATION) or "<CAP_COM>" in text
    parts, seen = [], set()
    for segment in languages.segments(text, selected_voice, main_lang, blended):
        raw = segment["text"].strip()
        # The switcher can repeat a short heading; speak each text once.
        if not raw or raw in seen:
            continue
        seen.add(raw)
        segment_lang = get_language_from_voice(segment["voice"])
        prepared = languages.prepare(segment["text"], segment, segment_lang, main_lang, selected_voice, bypassed)
        if prepared is None or not prepared[0].strip():
            continue
        segment_text, engine_lang = prepared

        if segment.get("is_fallback") or not blended:
            # A foreign fallback voice speaks clean of any blend.
            voice = segment["voice"] if voices and segment["voice"] in voices else selected_voice
        else:
            voice, engine_lang = selected_voice, main_lang
        try:
            if has_punctuation:
                samples, _ = synthesize_with_pauses(engine, segment_text, voice, speed, engine_lang,
                                                    pauses, behaviors, request.cache_audio)
                if samples is not None and len(samples.flatten()) > 0:
                    parts.append(samples.flatten())
            else:
                parts += _speak_chunks(engine, _unshield(segment_text), voice, speed, engine_lang,
                                       request.cache_audio)
        except Exception as e:
            print(f"[Engine] Segment skip protection triggered for '{segment_text[:10]}': {e}")

    samples = np.concatenate(parts) if parts else np.array([], dtype=np.float32)
    if front_ms > 0:
        samples = np.concatenate([_silence_pad(front_ms, SAMPLE_RATE), samples])
    if end_ms > 0:
        samples = np.concatenate([samples, _silence_pad(end_ms, SAMPLE_RATE)])
    if len(samples) == 0:
        samples = create_anti_skip_silence(100, SAMPLE_RATE)
    return Speech(samples, SAMPLE_RATE, bypassed)
