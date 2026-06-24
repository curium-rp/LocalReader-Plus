from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class LibraryItem(BaseModel):
    id: str
    fileName: str
    totalPages: int
    currentPage: int
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: int
    lastAccessed: float

class ContentItem(BaseModel):
    id: str
    pages: List[str]
    image_map: Optional[Dict[str, str]] = None
    toc_map: Optional[List[Dict[str, Any]]] = None
    currentPage: Optional[int] = 0
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: Optional[int] = 0
    lastAccessed: Optional[float] = 0.0

class PronunciationRule(BaseModel):
    id: str
    original: str
    replacement: str
    match_case: bool
    word_boundary: bool
    is_regex: Optional[bool] = False

class AppSettings(BaseModel):
    pronunciationRules: List[PronunciationRule]
    ignoreList: List[str]
    voice_id: Optional[str] = "af_heart"
    speed: Optional[float] = 1.0
    font_size: Optional[int] = 16
    header_footer_mode: Optional[str] = "off"
    engine_mode: Optional[str] = "gpu"
    ui_language: Optional[str] = "en"
    pause_settings: Optional[Dict[str, int]] = {
        "comma": 300,
        "period": 0,
        "question": 600,
        "exclamation": 600,
        "colon": 400,
        "semicolon": 400,
        "newline": 800,
    }
    behavior_settings: Optional[Dict[str, int]] = {
        "H": 2000,
        "Img": 3000,
        "S": 1000,
        "N": 500,
    }

class TimerRequest(BaseModel):
    minutes: int

class ExportRequest(BaseModel):
    doc_id: str
    voice: str = "af_heart"
    speed: float = 1.0
    rules: List[PronunciationRule]
    ignore_list: List[str] = []
    format: str = "wav"

class SynthesisRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    rules: List[PronunciationRule]
    ignore_list: List[str] = []
    pause_settings: Optional[Dict[str, int]] = {
        "comma": 300,
        "period": 0,
        "question": 600,
        "exclamation": 600,
        "colon": 400,
        "semicolon": 400,
        "newline": 800,
    }
    behavior_settings: Optional[Dict[str, int]] = {
        "H": 2000,
        "Img": 3000,
        "S": 1000,
        "N": 500,
    }
    behavior_type: Optional[str] = "N"