from fastapi import APIRouter
from pydantic import BaseModel, Field
import json

from ..config import view_file
from ..utils import safe_save_json

router = APIRouter()


class ReaderViewSettings(BaseModel):
    progressBar: bool = True
    pageCounter: bool = True
    percentage: bool = True
    clock: bool = True


class LibraryViewSettings(BaseModel):
    fraction: bool = True
    percentage: bool = True


class ViewSettings(BaseModel):
    reader: ReaderViewSettings = Field(default_factory=ReaderViewSettings)
    library: LibraryViewSettings = Field(default_factory=LibraryViewSettings)


DEFAULT_VIEW = ViewSettings().model_dump()


def _load_view():
    data = json.loads(json.dumps(DEFAULT_VIEW))
    if view_file.exists():
        try:
            with open(view_file, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                reader = stored.get("reader") if isinstance(stored.get("reader"), dict) else {}
                library = stored.get("library") if isinstance(stored.get("library"), dict) else {}
                data["reader"].update({k: bool(v) for k, v in reader.items() if k in data["reader"]})
                data["library"].update({k: bool(v) for k, v in library.items() if k in data["library"]})
        except Exception:
            pass
    return data


@router.get("/api/view")
async def get_view():
    return _load_view()


@router.post("/api/view")
async def save_view(settings: ViewSettings):
    payload = settings.model_dump()
    try:
        view_file.parent.mkdir(parents=True, exist_ok=True)
        safe_save_json(view_file, payload)
        return {"status": "success", **payload}
    except Exception as e:
        return {"error": str(e)}
