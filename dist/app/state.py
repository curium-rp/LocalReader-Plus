"""Process-wide state: the speech engine, the active blend, and job status."""

from speech_core import Blends, SpeechEngine

from .config import userdata_dir
from .utils import safe_save_json

engine = SpeechEngine()
blends = Blends(userdata_dir / "bleeding", safe_save_json)

system_status = {
    "is_loading": False,
    "last_error": None,
    "is_downloading": False,
    "downloading_model": None,
    "download_progress": 0,
    "download_stage": None,
}

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
