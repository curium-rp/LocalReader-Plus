"""Kokoro speech shared by LocalReader-Plus and FalcoType.

Self-contained: no imports from either app. Each app owns one SpeechEngine and
one Blends store, chooses which model to load, and wraps synthesis in its own
routes. FalcoType loads this package from the Reader's source tree
(reader_paths.load_speech_core), so both apps run the same code.

Exports load on first use: importing the package (or speech_core.hardware)
must not import ONNX Runtime, because the launchers link the GPU libraries
with hardware.link_execution_providers() first.
"""

import importlib

_EXPORTS = {
    "Blends": "blending",
    "SpeechEngine": "engine",
    "PLAIN": "pipeline", "PlainLanguages": "pipeline", "Speech": "pipeline",
    "SpeechUnavailable": "pipeline", "synthesize": "pipeline",
    "PronunciationRule": "request", "SynthesisRequest": "request",
    "pcm_response": "responses", "speak": "responses", "wav_response": "responses",
    "voice_catalog": "voices",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value
