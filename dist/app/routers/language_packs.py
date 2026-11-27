"""Language packages for a Reader that FalcoType launched.

A standalone Reader installs every package up front, so these routes answer
404 there and the manager shows no "Language packages" tab.
"""

from fastapi import APIRouter, HTTPException

from ..logic import language_packs
from ..runtime import runtime

router = APIRouter()


def require_submodule():
    if not runtime.submodule:
        raise HTTPException(status_code=404, detail="Language packages are part of the standalone install.")


@router.get("/api/language-packs")
def list_language_packs():
    require_submodule()
    return language_packs.installer.snapshot()


@router.post("/api/language-packs/{pack_id}/install")
def install_language_pack(pack_id: str):
    require_submodule()
    if pack_id not in language_packs.PACKS:
        raise HTTPException(status_code=404, detail="Unknown language package.")
    try:
        language_packs.installer.start(pack_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return language_packs.installer.snapshot()
