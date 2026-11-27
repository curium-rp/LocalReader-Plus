from fastapi import APIRouter, HTTPException, BackgroundTasks
from ..state import system_status
from .. import state
from ..utils import safe_save_json
from ..config import settings_file
from ..models import ModelDownloadRequest, ModelSelectRequest, ModelDeleteRequest
from typing import Optional
import json
from ..runtime import runtime

from ..logic import downloader, language_packs
from ..logic.update_notice import pending_notice
from ..logic.downloader import (
    get_bundle, get_default_installed_bundle_id, get_catalog_summary, delete_bundle_files,
    download_model_bundle, get_available_models, classify_network_error,
)

if runtime.submodule:
    downloader.MODELS_DIR = runtime.models_dir
    downloader.MODELS_READ_ONLY = True

router = APIRouter()


def require_model_management():
    if runtime.submodule:
        raise HTTPException(status_code=403, detail="Shared models are managed by FalcoType, not LocalReader.")


def load_engine_logic(requested_model_id=None, requested_mode=None):
    """Pick the model and hardware from the Reader's settings, then load it."""
    system_status["is_loading"] = True
    try:
        _load_engine(requested_model_id, requested_mode)
    finally:
        system_status["is_loading"] = False


def _read_settings():
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_engine(requested_model_id, requested_mode):
    settings = _read_settings()
    # A managed reader always loads FalcoType's root kokoro.onnx and voices.bin.
    if runtime.submodule:
        requested_model_id = "kokoro-v1.0"
    elif requested_model_id is None and settings is not None:
        requested_model_id = settings.get("selected_model") or (
            "kokoro-v1.0-int8" if settings.get("engine_mode") == "cpu" else "kokoro-v1.0")

    requested_model_id = {"gpu": "kokoro-v1.0", "cpu": "kokoro-v1.0-int8"}.get(requested_model_id, requested_model_id)
    if requested_model_id == "kokoro-v1.0-int8":
        int8_bundle = get_bundle("kokoro-v1.0-int8")
        if not int8_bundle or not int8_bundle.is_installed():
            print("[ENGINE] kokoro.int8.onnx not found. Falling back to Kokoro v1.0 (FP32) on CPU.")
            requested_model_id, requested_mode = "kokoro-v1.0", "cpu"
    requested_model_id = requested_model_id or get_default_installed_bundle_id()

    bundle = get_bundle(requested_model_id) if requested_model_id else None
    if (not bundle or not bundle.is_installed()) and not runtime.submodule:
        fallback_id = get_default_installed_bundle_id()
        if fallback_id:
            bundle = get_bundle(fallback_id)

    if not bundle or not bundle.is_installed():
        system_status["last_error"] = (
            f"FalcoType's kokoro.onnx or voices.bin is missing or incomplete in {runtime.models_dir}. "
            "Manage models in FalcoType."
            if runtime.submodule else "No TTS voice models found. Please download a model."
        )
        return

    voices_path = bundle.get_voices_path()
    if not voices_path.exists():
        system_status["last_error"] = (
            "FalcoType's voices.bin is missing. Manage models in FalcoType."
            if runtime.submodule else f"Voice pack missing for {bundle.name}. Please re-download."
        )
        return

    if requested_mode is None:
        requested_mode = (settings or {}).get("engine_mode", "gpu")

    try:
        state.engine.load(bundle.id, bundle.get_model_path(), voices_path, requested_mode)
    except Exception as e:
        system_status["last_error"] = f"Failed to load TTS engine: {str(e)}"
        print(f"[ENGINE ERROR] {system_status['last_error']}")
        import traceback
        traceback.print_exc()
        return
    system_status["last_error"] = None

    # A voice the new model lacks is cleared; the user picks again. Settings that
    # cannot be read are left alone, never replaced by these two keys.
    try:
        current = _read_settings()
        if current is not None and current.get("voice_id") not in state.engine.voices():
            current["voice_id"] = ""
            current["selected_model"] = bundle.id
            safe_save_json(settings_file, current)
            print(f"[ENGINE] Active voice reset for {bundle.name} (user manages selection).")
    except Exception as err:
        print(f"[ENGINE] Failed to sync voice settings: {err}")


@router.get("/api/system/update-notice")
def get_update_notice():
    # Sync: the GitHub request runs in the threadpool. Inside FalcoType (Book mode)
    # only FalcoType announces updates, so the managed Reader never checks.
    return {"notice": None if runtime.submodule else pending_notice()}


# --- System Status Endpoints ---
@router.get("/api/system/status")
async def get_status():
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
        current_engine_mode = settings.get("engine_mode", "gpu")
        selected_model = settings.get("selected_model", "kokoro-v1.0")
    except Exception:
        current_engine_mode = "gpu"
        selected_model = "kokoro-v1.0"

    if runtime.submodule:
        selected_model = "kokoro-v1.0"
    available_models = get_available_models()

    return {
        "submodule_mode": runtime.submodule,
        "downloads_allowed": not runtime.submodule,
        "models_read_only": runtime.submodule,
        # Read first: the loader sets the model before it clears this, so the pair never looks failed.
        "is_loading": system_status["is_loading"],
        "model_loaded": state.engine.loaded,
        "is_downloading": system_status["is_downloading"],
        "downloading_model": system_status.get("downloading_model"),
        "download_progress": system_status.get("download_progress", 0),
        "download_stage": system_status.get("download_stage"),
        "last_error": system_status["last_error"],
        "voices": state.engine.voices(),
        "engine_mode": current_engine_mode,
        "selected_model": selected_model,
        # Only a model running in memory is active.
        "active_model": state.engine.model_id,
        "active_hardware": state.engine.hardware,
        "available_models": available_models,
        "language_packs": {pack: language_packs.ready(pack) for pack in language_packs.PACKS},
    }


# --- Model Management Endpoints ---
@router.get("/api/system/models")
async def list_models():
    # Only a model running in memory is "In Use".
    active_id = state.engine.model_id
    catalog = get_catalog_summary(active_id)
    return {
        "submodule_mode": runtime.submodule,
        "downloads_allowed": not runtime.submodule,
        "models_read_only": runtime.submodule,
        "active_model": active_id,
        "is_loading": system_status["is_loading"],
        "is_downloading": system_status["is_downloading"],
        "downloading_model": system_status.get("downloading_model"),
        "download_progress": system_status.get("download_progress", 0),
        "download_stage": system_status.get("download_stage"),
        "last_error": system_status.get("last_error"),
        "active_hardware": state.engine.hardware,
        "models": catalog,
    }


@router.post("/api/system/models/download")
async def download_model_endpoint(request: ModelDownloadRequest, background_tasks: BackgroundTasks):
    require_model_management()
    model_id = request.model_id or request.model_type or "kokoro-v1.0"
    if model_id == "gpu":
        model_id = "kokoro-v1.0"
    elif model_id == "cpu":
        model_id = "kokoro-v1.0-int8"

    bundle = get_bundle(model_id)
    if not bundle:
        raise HTTPException(status_code=400, detail=f"Unknown model ID: {model_id}")

    if system_status["is_downloading"]:
        return {"status": "busy", "message": "Another download is already in progress"}

    if bundle.is_installed():
        return {"status": "already_installed", "message": f"{bundle.name} is already installed"}

    def task():
        system_status["is_downloading"] = True
        system_status["downloading_model"] = model_id
        system_status["download_progress"] = 0
        system_status["download_stage"] = "Starting..."
        system_status["last_error"] = None

        def on_progress(stage: str, dl: int, tot: int):
            pct = int((dl / tot) * 100) if tot > 0 else 0
            system_status["download_progress"] = pct
            system_status["download_stage"] = f"Downloading {stage} ({pct}%)"

        try:
            print(f"[SETUP] Starting download for bundle {model_id}...")
            download_model_bundle(model_id, progress_callback=on_progress)
            print(f"[SETUP] Download complete for bundle {model_id}")

            if not state.engine.loaded:
                load_engine_logic(requested_model_id=model_id)
        except Exception as e:
            msg = classify_network_error(e)
            system_status["last_error"] = msg
            print(f"[SETUP ERROR] {msg}")
        finally:
            system_status["is_downloading"] = False
            system_status["downloading_model"] = None
            system_status["download_progress"] = 0
            system_status["download_stage"] = None

    background_tasks.add_task(task)
    return {"status": "started", "model_id": model_id}


@router.post("/api/system/models/select")
async def select_model_endpoint(request: ModelSelectRequest, background_tasks: BackgroundTasks):
    model_id = request.model_id
    if runtime.submodule and model_id not in ("gpu", "kokoro-v1.0"):
        raise HTTPException(status_code=403, detail="The managed reader uses FalcoType's kokoro.onnx.")
    if model_id == "gpu":
        model_id = "kokoro-v1.0"
    if model_id == "kokoro-v1.0-int8":
        int8_bundle = get_bundle("kokoro-v1.0-int8")
        if not int8_bundle or not int8_bundle.is_installed():
            v1_bundle = get_bundle("kokoro-v1.0")
            if v1_bundle and v1_bundle.is_installed():
                model_id = "kokoro-v1.0"

    bundle = get_bundle(model_id)
    if not bundle:
        raise HTTPException(status_code=400, detail=f"Unknown model ID: {model_id}")

    if not bundle.is_installed():
        return {
            "status": "model_missing",
            "message": f"{bundle.name} is not installed. Please download it first.",
        }

    if system_status["is_downloading"]:
        return {"status": "busy", "message": "Cannot switch models while downloading"}

    # Persist selected_model in settings.json
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
        settings["selected_model"] = model_id
        safe_save_json(settings_file, settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def reload_task():
        if system_status["is_loading"]:
            return
        load_engine_logic(requested_model_id=model_id)

    background_tasks.add_task(reload_task)
    return {
        "status": "switching",
        "selected_model": model_id,
        "default_voice": "",
        "message": f"Switching to {bundle.name}...",
    }


@router.post("/api/system/models/delete")
async def delete_model_endpoint(request: ModelDeleteRequest):
    require_model_management()
    model_id = request.model_id
    bundle = get_bundle(model_id)
    if not bundle:
        raise HTTPException(status_code=400, detail=f"Unknown model ID: {model_id}")

    if system_status["is_downloading"] and system_status.get("downloading_model") == model_id:
        raise HTTPException(status_code=400, detail="Cannot delete while downloading")


    # Protect the currently active in-use model from deletion.
    # The UI hides the delete button, but this is the backend guard.
    is_active = state.engine.model_id == model_id
    if is_active:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete '{bundle.name}' while it is in use. Switch to another model first.",
        )

    success, message = delete_bundle_files(model_id)
    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {"status": "deleted", "model_id": model_id, "message": message}


# --- Legacy Backward Compatibility Endpoints ---
@router.post("/api/system/setup")
async def run_setup(background_tasks: BackgroundTasks, model_type: Optional[str] = None):
    require_model_management()
    if system_status["is_downloading"]:
        return {"status": "already_running"}

    target = model_type or "kokoro-v1.0"
    if target == "gpu":
        target = "kokoro-v1.0"
    elif target == "cpu":
        target = "kokoro-v1.0-int8"

    def setup_task():
        system_status["is_downloading"] = True
        system_status["downloading_model"] = target
        system_status["download_progress"] = 0
        system_status["download_stage"] = "Starting..."
        system_status["last_error"] = None

        def on_progress(stage: str, dl: int, tot: int):
            pct = int((dl / tot) * 100) if tot > 0 else 0
            system_status["download_progress"] = pct
            system_status["download_stage"] = f"Downloading {stage} ({pct}%)"

        try:
            print(f"[SETUP] Starting download for {target}...")
            download_model_bundle(target, progress_callback=on_progress)
            print("[SETUP] Download complete, loading engine...")
            load_engine_logic(requested_model_id=target)
            print("[SETUP] Setup complete!")
        except Exception as e:
            msg = classify_network_error(e)
            system_status["last_error"] = msg
            print(f"[SETUP ERROR] {msg}")
        finally:
            system_status["is_downloading"] = False
            system_status["downloading_model"] = None
            system_status["download_progress"] = 0
            system_status["download_stage"] = None

    background_tasks.add_task(setup_task)
    return {"status": "started"}


@router.post("/api/system/switch-engine")
async def switch_engine(background_tasks: BackgroundTasks, target_mode: str):
    if runtime.submodule and target_mode not in ("gpu", "kokoro-v1.0"):
        raise HTTPException(status_code=403, detail="The managed reader uses FalcoType's kokoro.onnx.")
    if target_mode not in ["gpu", "cpu", "kokoro-v1.0", "kokoro-v1.0-int8", "kokoro-v1.1"]:
        raise HTTPException(status_code=400, detail="Invalid engine mode or model ID")

    if target_mode in ["gpu", "cpu"]:
        model_id = "kokoro-v1.0" if target_mode == "gpu" else "kokoro-v1.0-int8"
    else:
        model_id = target_mode

    bundle = get_bundle(model_id)
    if not bundle or not bundle.is_installed():
        return {
            "status": "model_missing",
            "message": f"Model {model_id} not downloaded.",
            "requires_download": True,
        }

    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
        settings["selected_model"] = model_id
        safe_save_json(settings_file, settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def reload_task():
        if system_status["is_loading"]:
            return
        load_engine_logic(requested_model_id=model_id)

    background_tasks.add_task(reload_task)
    return {
        "status": "switching",
        "target_mode": target_mode,
        "message": f"Switching to {model_id}...",
    }


@router.post("/api/system/download-model")
async def download_specific_model(
    background_tasks: BackgroundTasks,
    model_type: Optional[str] = None,
    payload: Optional[ModelDownloadRequest] = None,
):
    require_model_management()
    target = model_type
    if not target and payload:
        target = payload.model_id or payload.model_type
    if not target:
        target = "kokoro-v1.1"

    if target == "gpu":
        target = "kokoro-v1.0"
    elif target == "cpu":
        target = "kokoro-v1.0-int8"

    bundle = get_bundle(target)
    if not bundle:
        raise HTTPException(status_code=400, detail="Invalid model type")

    if system_status["is_downloading"]:
        return {"status": "already_downloading"}

    if bundle.is_installed():
        return {"status": "already_exists", "message": "Model already downloaded"}

    def download_task():
        system_status["is_downloading"] = True
        system_status["downloading_model"] = target
        system_status["download_progress"] = 0
        system_status["download_stage"] = "Starting..."
        system_status["last_error"] = None

        def on_progress(stage: str, dl: int, tot: int):
            pct = int((dl / tot) * 100) if tot > 0 else 0
            system_status["download_progress"] = pct
            system_status["download_stage"] = f"Downloading {stage} ({pct}%)"

        try:
            download_model_bundle(target, progress_callback=on_progress)
        except Exception as e:
            system_status["last_error"] = str(e)
        finally:
            system_status["is_downloading"] = False
            system_status["downloading_model"] = None
            system_status["download_progress"] = 0
            system_status["download_stage"] = None

    background_tasks.add_task(download_task)
    return {"status": "started"}


@router.post("/api/system/clear-cache")
async def clear_all_cache():
    return {"status": "success", "message": "No audio cache"}
