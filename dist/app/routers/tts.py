import json
from typing import Literal

from fastapi import APIRouter

from speech_core import SynthesisRequest, speak, synthesize, voice_catalog

from ..config import base_dir
from ..logic.speech_languages import ReaderLanguages
from ..runtime import runtime
from .. import state

router = APIRouter()

UNAVAILABLE = ("FalcoType's shared TTS model is unavailable. Check model setup in FalcoType."
               if runtime.submodule else "TTS Engine not initialized.")


def reader_speech(request: SynthesisRequest):
    """The Reader's own speech: its active blend and its language handling."""
    return synthesize(state.engine, request, state.blends.active_blend,
                      ReaderLanguages(state.engine.model_id))


@router.get("/api/voices/available")
def get_voices():
    return {
        "categories": voice_catalog(state.engine.voices()),
        "blend": {"enabled": bool(state.blends.active), "expression": state.blends.active},
    }


@router.get("/api/locale/{lang}")
async def get_locale(lang: str):
    locale_dir = base_dir / "locales"
    file_path = locale_dir / f"{lang}.json"
    if not file_path.exists(): file_path = locale_dir / "en.json"
    try:
        with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


@router.post("/api/synthesize")
# FastAPI runs synchronous handlers in its worker pool; inference must not block control/lease requests.
def synthesize_route(request: SynthesisRequest, audio_format: Literal["wav", "pcm_f32le"] = "wav"):
    return speak(lambda: reader_speech(request), audio_format, UNAVAILABLE)
