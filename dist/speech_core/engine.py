"""One process's Kokoro engine: loading, unloading, and locked, cached generation."""

import gc
import hashlib
import os
import threading
import time
from collections import OrderedDict

from .hardware import configured_providers
from .kokoro import PatchedKokoro
from .tts_helpers import create_anti_skip_silence, is_blend_expression

AUDIO_CACHE_SIZE = 20
GPU_RETRIES = 3

_CUDA_OPTIONS = {
    "device_id": 0,
    "cudnn_conv_algo_search": "DEFAULT",
    "arena_extend_strategy": "kSameAsRequested",
    "cudnn_conv_use_max_workspace": "0",
    "do_copy_in_default_stream": "1",
    "has_user_compute_stream": "0",
}


class SpeechEngine:
    """Holds at most one Kokoro model. Callers own model selection and settings;
    the engine owns the ONNX session setup, the inference lock, and the audio cache."""

    def __init__(self):
        self.kokoro = None
        self.model_id = None
        self.hardware = "cpu"
        # Inference is drip-fed one request at a time to keep GPU/CPU use low.
        self._inference_lock = threading.Lock()
        self._cache = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def loaded(self):
        return self.kokoro is not None

    def voices(self):
        kokoro = self.kokoro
        return kokoro.get_voices() if kokoro is not None else []

    def load(self, model_id, model_path, voices_path, mode, providers=None):
        """Replace the current model. `mode` is "gpu" or "cpu"; `providers` default to
        the ones the launcher linked. Returns the hardware actually used."""
        providers = configured_providers() if providers is None else providers
        # Keep C-level math libraries from spawning hidden threads.
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[name] = "1"

        import onnxruntime as ort
        try:
            ort.set_default_logger_severity(3)
        except AttributeError:
            pass

        if self.kokoro is not None:
            print("[ENGINE] Unloading previous model...")
            self.unload()

        available = ort.get_available_providers()
        gpu_providers = [p for p in providers if p in available and p != "CPUExecutionProvider"]
        if mode == "gpu" and not gpu_providers:
            print(" -> [WARNING] No active GPU execution provider available. Fallback to CPU path.")
            mode = "cpu"

        print(f"[ENGINE] Loading Model: {model_id} on Hardware: {mode.upper()}...")
        print(f" -> Model Path: {model_path}")
        print(f" -> Voices Path: {voices_path}")

        if mode == "gpu":
            kokoro = self._load_on_gpu(ort, [p for p in providers if p in available], model_path, voices_path)
        else:
            print("[ENGINE] Loading Model in \033[92mRAM (CPU)\033[0m...")
            kokoro = PatchedKokoro(str(model_path), str(voices_path))

        self.kokoro, self.model_id, self.hardware = kokoro, model_id, mode
        print(f"[ENGINE] Successfully loaded {model_id} with {len(kokoro.get_voices())} voices.")
        return mode

    @staticmethod
    def _load_on_gpu(ort, providers, model_path, voices_path):
        session_providers = [("CUDAExecutionProvider", _CUDA_OPTIONS) if p == "CUDAExecutionProvider" else p
                             for p in providers]
        names = [p.replace("ExecutionProvider", "") for p in providers]
        print(f" -> [ENGINE] Active Hardware Linked: \033[92m{' | '.join(names)}\033[0m")

        original_session = ort.InferenceSession

        def forced_gpu_session(*args, **kwargs):
            kwargs["providers"] = session_providers
            options = kwargs.get("sess_options") or ort.SessionOptions()
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
            kwargs["sess_options"] = options
            return original_session(*args, **kwargs)

        ort.InferenceSession = forced_gpu_session
        try:
            return PatchedKokoro(str(model_path), str(voices_path))
        except Exception as e:
            print(f"[ENGINE WARNING] PatchedKokoro failed: {e}. Trying standard Kokoro fallback.")
            ort.InferenceSession = original_session
            from kokoro_onnx import Kokoro
            return Kokoro(str(model_path), str(voices_path))
        finally:
            ort.InferenceSession = original_session

    def unload(self):
        """Drop the model and everything derived from it, and return its memory now."""
        self.kokoro, self.model_id = None, None
        self.clear_cache()
        gc.collect()
        gc.collect()

    def clear_cache(self):
        with self._cache_lock:
            self._cache.clear()

    def generate(self, text, voice, speed, lang, cache=True):
        """(samples, sample_rate) for one chunk of text. A failed chunk is silence, so
        a long read never stops on one bad sentence."""
        kokoro = self.kokoro
        if kokoro is None:
            raise RuntimeError("TTS engine is not loaded.")
        key = hashlib.md5(f"{text}|{voice}|{speed}|{lang}".encode("utf-8")).hexdigest()
        if cache:
            with self._cache_lock:
                if key in self._cache:
                    return self._cache[key]

        for attempt in range(GPU_RETRIES):
            try:
                target = kokoro.get_voice_style(voice) if isinstance(voice, str) and is_blend_expression(voice) else voice
                with self._inference_lock:
                    audio = kokoro.create(text, target, speed, lang)
                if cache and audio is not None and len(audio[0].flatten()) > 0:
                    with self._cache_lock:
                        # A model swapped in meanwhile must not inherit this model's audio.
                        if self.kokoro is kokoro:
                            if len(self._cache) >= AUDIO_CACHE_SIZE:
                                self._cache.popitem(last=False)
                            self._cache[key] = audio
                # Sweep the PyBind11 pointers ONNX Runtime leaves behind.
                gc.collect()
                return audio
            except Exception as e:
                message = str(e).lower()
                transient = "cuda" in message or "status code" in message or "kernel" in message
                if transient and attempt < GPU_RETRIES - 1:
                    # GPU clock jumps cause transient CUDA/ONNX failures; let the power state settle.
                    delay = 0.15 * (attempt + 1)
                    print(f"[Hardware Shield] GPU transient error ({e}). Retrying {attempt + 2}/{GPU_RETRIES} in {delay}s")
                    time.sleep(delay)
                    continue
                print(f"[Engine] Audio generation failed: {e}. Bypassing with silence.")
                return create_anti_skip_silence(500, 24000), 24000
