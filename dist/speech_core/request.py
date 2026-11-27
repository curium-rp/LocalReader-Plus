"""The speech request both apps accept, and the bridge carries unchanged."""

import uuid
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PronunciationRule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original: str
    replacement: str
    match_case: bool = False
    word_boundary: bool = True
    is_regex: Optional[bool] = False


DEFAULT_PAUSES = {
    "comma": 0, "period": 0, "spam": 0, "question": 600, "exclamation": 600,
    "colon": 400, "semicolon": 400, "newline": 800,
}
# Pauses around a unit by its kind: heading, image, scene break, normal text.
DEFAULT_BEHAVIORS = {"H": 2000, "Img": 3000, "S": 1000, "N": 500}


class SynthesisRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    rules: List[PronunciationRule]
    ignore_list: List[str] = []
    pause_settings: Optional[Dict[str, int]] = dict(DEFAULT_PAUSES)
    behavior_settings: Optional[Dict[str, int]] = dict(DEFAULT_BEHAVIORS)
    behavior_type: Optional[str] = "N"
    # False keeps this request out of the engine's audio cache (a voice test).
    cache_audio: bool = True
