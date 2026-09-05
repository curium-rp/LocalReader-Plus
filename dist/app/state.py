import threading
import time
import os
import numpy as np
import sys
from typing import Optional, Dict
from kokoro_onnx import Kokoro, MAX_PHONEME_LENGTH, SAMPLE_RATE

# --- Global State Instances ---
kokoro = None  # The TTS engine instance

system_status = {"is_loading": False, "last_error": None, "is_downloading": False, "downloading_model": None}

export_status = {
    "is_exporting": False,
    "progress": 0,
    "total": 0,
    "error": None,
    "output_file": None,
}

ffmpeg_status = {
    "is_installed": False,
    "is_downloading": False,
    "progress": 0,
    "total": 0,
    "error": None,
    "message": "",
}


# --- PatchedKokoro Class ---
class PatchedKokoro(Kokoro):
    """
    Patched version supporting both FP32 (GPU/CPU) and INT8 (CPU) models.
    """

    def get_voices(self):
        # Explicit delegation to ensure it works
        voices = super().get_voices()
        return voices

    def _create_audio(self, phonemes: str, voice: np.ndarray, speed: float):
        phonemes = phonemes[:MAX_PHONEME_LENGTH]
        tokens = np.array(self.tokenizer.tokenize(phonemes), dtype=np.int64)

        if len(tokens) == 0:
            print(f"[PatchedKokoro] Warning: No tokens for phonemes '{phonemes}'")
            return np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE

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
        return self.tokenizer.phonemize(text, lang)

    def create(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us"):
        try:
            phonemes = self.phonemize(text, lang)
            if not phonemes or not phonemes.strip():
                return np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE

            if len(phonemes) <= MAX_PHONEME_LENGTH:
                audio, rate = self._create_audio(
                    phonemes, self.get_voice_style(voice), speed
                )
                if audio.size == 0:
                    return (
                        np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32),
                        SAMPLE_RATE,
                    )
                return audio, rate

            return super().create(text, voice, speed, lang)

        except ValueError as e:
            if "need at least one array to concatenate" in str(e):
                try:
                    clean_text = "".join(c for c in text if c.isalnum() or c.isspace())
                    if clean_text.strip() and clean_text != text:
                        return super().create(clean_text, voice, speed, lang)
                except Exception:
                    pass
                return np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE
            raise e
        except Exception:
            return np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE


# --- SleepTimer Class ---
class SleepTimer:
    def __init__(self):
        self.active = False
        self.target_time = 0
        self.duration_seconds = 0
        self.timer_thread: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def set_timer(self, minutes: int):
        with self._lock:
            self.stop_timer_internal()

            if minutes <= 0:
                return

            self.duration_seconds = minutes * 60
            self.target_time = time.time() + self.duration_seconds
            self.active = True

            self.timer_thread = threading.Timer(
                self.duration_seconds, self.trigger_shutdown
            )
            self.timer_thread.daemon = True
            self.timer_thread.start()
            print(f"[TIMER] Sleep timer set for {minutes} minutes")

    def stop_timer(self):
        with self._lock:
            self.stop_timer_internal()
            print("[TIMER] Sleep timer stopped")

    def stop_timer_internal(self):
        if self.timer_thread:
            self.timer_thread.cancel()
            self.timer_thread = None
        self.active = False
        self.target_time = 0
        self.duration_seconds = 0

    def trigger_shutdown(self):
        print("[TIMER] Time's up! (Sleep timer expired)")
        with self._lock:
            self.active = False

    def get_status(self):
        with self._lock:
            if not self.active:
                return {"active": False, "remaining_seconds": 0}

            remaining = self.target_time - time.time()
            if remaining <= 0:
                return {"active": False, "remaining_seconds": 0}

            return {
                "active": True,
                "remaining_seconds": int(remaining),
                "total_seconds": self.duration_seconds,
            }


sleep_timer = SleepTimer()
