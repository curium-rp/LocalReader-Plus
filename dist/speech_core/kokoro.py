"""Kokoro with blend voices, the shared eSpeak lock, and FP32/INT8 model support."""

import numpy as np
from kokoro_onnx import Kokoro, MAX_PHONEME_LENGTH, SAMPLE_RATE

from .syllable import phonemizer_lock
from .tts_helpers import is_blend_expression, parse_blend_expression


def _short_silence():
    return np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE


class PatchedKokoro(Kokoro):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Blend style vectors belong to this model's voices, so they leave with it.
        self._blend_styles = {}

    def fallback_voice(self, voices=None):
        voices = self.get_voices() if voices is None else voices
        if not voices:
            raise KeyError("No voices available in model")
        return next((v for v in ("af_heart", "af_maple") if v in voices), voices[0])

    def get_voice_style(self, voice: str):
        if is_blend_expression(voice):
            return self._blend_style(voice)
        voices = self.get_voices()
        if voice not in voices:
            print(f"[PatchedKokoro] Warning: Voice '{voice}' not in loaded model. Falling back.")
            voice = self.fallback_voice(voices)
        return super().get_voice_style(voice)

    def _blend_style(self, expr: str) -> np.ndarray:
        """Weighted sum of the blend's voice styles, shape (510, 1, 256)."""
        slots = parse_blend_expression(expr)
        voices = self.get_voices()
        fallback = self.fallback_voice(voices) if voices else "af_heart"
        if not slots:
            return super().get_voice_style(fallback)
        key = "+".join(f"{v}({w:.4f})" for v, w in slots)
        cached = self._blend_styles.get(key)
        if cached is not None:
            return cached
        blended = sum(
            super(PatchedKokoro, self).get_voice_style(v if v in voices else fallback).astype(np.float32) * float(w)
            for v, w in slots
        )
        self._blend_styles[key] = blended
        return blended

    def _create_audio(self, phonemes: str, voice: np.ndarray, speed: float):
        phonemes = phonemes[:MAX_PHONEME_LENGTH]
        tokens = np.array(self.tokenizer.tokenize(phonemes), dtype=np.int64)

        if len(tokens) == 0:
            print(f"[PatchedKokoro] Warning: No tokens for phonemes '{phonemes}'")
            return _short_silence()

        style_idx = min(len(tokens), len(voice) - 1)
        voice_style = voice[style_idx]
        tokens_batch = [[0, *tokens, 0]]

        token_key = getattr(
            self,
            "_tokens_input",
            "tokens" if "tokens" in [i.name for i in self.sess.get_inputs()] else "input_ids",
        )
        token_dtype = getattr(self, "_input_dtypes", {}).get(token_key, np.int64)
        speed_val = self._speed_value(speed) if hasattr(self, "_speed_value") else speed

        inputs = {
            token_key: np.array(tokens_batch, dtype=token_dtype),
            "style": np.array(voice_style, dtype=np.float32),
            "speed": np.array([speed_val], dtype=np.float32),
        }
        audio = self.sess.run(None, inputs)[0]
        audio = np.asarray(audio).squeeze()
        return audio, SAMPLE_RATE

    def phonemize(self, text: str, lang: str):
        # Estimation/normalization workers also use the cached eSpeak backend.
        with phonemizer_lock:
            return self.tokenizer.phonemize(text, lang)

    def _prepare(
        self,
        text: str,
        voice: str | np.ndarray,
        speed: float,
        lang: str,
        is_phonemes: bool = False,
        sentence_pause: float = 0.25,
        clause_pause: float = 0.1,
    ):
        if isinstance(voice, str):
            voice = self.get_voice_style(voice)
        # Long-text fallback calls the base tokenizer directly through _prepare.
        with phonemizer_lock:
            return super()._prepare(
                text, voice, speed, lang, is_phonemes, sentence_pause, clause_pause
            )

    def create(self, text: str, voice: str | np.ndarray, speed: float = 1.0, lang: str = "en-us"):
        try:
            phonemes = self.phonemize(text, lang)
            if not phonemes or not phonemes.strip():
                return _short_silence()

            style_vector = self.get_voice_style(voice) if isinstance(voice, str) else voice

            if len(phonemes) <= MAX_PHONEME_LENGTH:
                audio, rate = self._create_audio(phonemes, style_vector, speed)
                return _short_silence() if audio.size == 0 else (audio, rate)

            return super().create(text, style_vector, speed, lang)

        except ValueError as e:
            if "need at least one array to concatenate" in str(e):
                try:
                    clean_text = "".join(c for c in text if c.isalnum() or c.isspace())
                    if clean_text.strip() and clean_text != text:
                        style_vector = self.get_voice_style(voice) if isinstance(voice, str) else voice
                        return super().create(clean_text, style_vector, speed, lang)
                except Exception:
                    pass
                return _short_silence()
            raise e
        except Exception as e:
            print(f"[PatchedKokoro] Create failed for text '{text[:30]}...': {e}")
            return _short_silence()
