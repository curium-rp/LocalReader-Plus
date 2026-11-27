from pydantic import BaseModel
from typing import List, Optional, Dict
from speech_core import PronunciationRule

class LibraryItem(BaseModel):
    id: str
    fileName: str
    totalPages: int
    currentPage: int
    lastSentenceId: Optional[str] = None
    lastSentenceIndex: int
    lastAccessed: float
    language: Optional[str] = None
    bookType: Optional[str] = None
    current_page: Optional[int] = None
    total_pages: Optional[int] = None
    progress_percent: Optional[int] = None
    disable_br: Optional[bool] = False

class AppSettings(BaseModel):
    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # Rules now live in userdata/pronunciationrules.json. Kept as optional to allow smooth migration from legacy settings.json.
    pronunciationRules: Optional[List[PronunciationRule]] = None
    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # Ignore list now lives in userdata/ignore.json. Kept as optional to allow smooth migration from legacy settings.json.
    ignoreList: Optional[List[str]] = None
    voice_id: Optional[str] = "af_heart"
    speed: Optional[float] = 1.0
    font_size: Optional[int] = 18
    engine_mode: Optional[str] = "gpu"
    selected_model: Optional[str] = "kokoro-v1.0"
    ui_language: Optional[str] = "en"
    pause_settings: Optional[Dict[str, int]] = {
        "comma": 0,
        "period": 0,
        "spam": 0,
        "question": 600,
        "exclamation": 600,
        "colon": 400,
        "semicolon": 400,
        "newline": 0,
    }
    behavior_settings: Optional[Dict[str, int]] = {
        "H": 2000,
        "Img": 3000,
        "S": 1000,
        "N": 500,
    }

class ModelDownloadRequest(BaseModel):
    model_id: Optional[str] = None
    model_type: Optional[str] = None

class ModelSelectRequest(BaseModel):
    model_id: str

class ModelDeleteRequest(BaseModel):
    model_id: str

class ExportRequest(BaseModel):
    doc_id: str
    voice: str = "af_heart"
    speed: float = 1.0
    rules: List[PronunciationRule]
    ignore_list: List[str] = []
    format: str = "wav"
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    start_tts_id: Optional[str] = None
    end_tts_id: Optional[str] = None
    pause_settings: Optional[Dict[str, int]] = None
    behavior_settings: Optional[Dict[str, int]] = None
    file_label: Optional[str] = "Full Book"
