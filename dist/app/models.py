from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Restored: Required for library.py
class LibraryItem(BaseModel):
    id: str
    fileName: str
    totalPages: int
    currentPage: int
    lastSentenceIndex: int
    lastAccessed: float

# Restored: Required for library.py
class ContentItem(BaseModel):
    id: str
    pages: List[str]

# Core Pronunciation Model
class PronunciationRule(BaseModel):
    id: str
    original: str
    replacement: str
    match_case: bool
    word_boundary: bool
    is_regex: Optional[bool] = False

# Restored: Required for settings.py (This fixes the ImportError)
class AppSettings(BaseModel):
    pronunciationRules: List[PronunciationRule]
    ignoreList: List[str]
    voice_id: Optional[str] = "af_bella"
    speed: Optional[float] = 1.0
    font_size: Optional[int] = 16
    header_footer_mode: Optional[str] = "off"
    engine_mode: Optional[str] = "gpu"
    ui_language: Optional[str] = "en"
    use_upscaler: Optional[bool] = False # Allows Database to remember toggle state
    pause_settings: Optional[Dict[str, int]] = {
        "comma": 300,
        "period": 600,
        "question": 600,
        "exclamation": 600,
        "colon": 400,
        "semicolon": 400,
        "newline": 800,
    }

# Failsafe for settings updates
class SettingsUpdate(BaseModel):
    pronunciationRules: Optional[List[PronunciationRule]] = None
    ignoreList: Optional[List[str]] = None
    voice_id: Optional[str] = None
    speed: Optional[float] = None
    font_size: Optional[int] = None
    header_footer_mode: Optional[str] = None
    engine_mode: Optional[str] = None
    pause_settings: Optional[Dict[str, int]] = None
    ui_language: Optional[str] = None
    use_upscaler: Optional[bool] = False

# Restored: Required for timer.py
class TimerRequest(BaseModel):
    minutes: int

# Restored: Required for export.py
class ExportRequest(BaseModel):
    doc_id: str
    rules: List[PronunciationRule]
    voice: str = "af_bella"
    speed: float = 1.0
    ignore_list: List[str] = []
    format: str = "wav"  

# Fully Authorized Payload for tts.py
class SynthesisRequest(BaseModel):
    text: str
    rules: List[PronunciationRule]
    voice: str = "af_sky"
    speed: float = 1.0
    ignore_list: List[str] = []
    use_upscaler: Optional[bool] = False # Allows tts.py to trigger LavaSR
    pause_settings: Optional[Dict[str, int]] = {
        "comma": 300,
        "period": 600,
        "question": 600,
        "exclamation": 600,
        "colon": 400,
        "semicolon": 400,
        "newline": 800,
    }