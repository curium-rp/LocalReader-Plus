from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from ..state import kokoro, system_status, PatchedKokoro
from ..utils import safe_save_json, has_onnxruntime_gpu
from ..config import base_dir, settings_file, get_app_anchored_path
from ..models import ModelDownloadRequest, ModelSelectRequest, ModelDeleteRequest
from typing import Optional, Dict, Any
import json
import sys
import os
import gc
from pathlib import Path

# Add app logic to path for imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

try:
    from logic.downloader import (
        MODEL_CATALOG,
        get_bundle,
        resolve_bundle_paths,
        get_default_installed_bundle_id,
        get_catalog_summary,
        delete_bundle_files,
        download_kokoro_model,
        download_model_bundle,
        check_model_exists,
        get_available_models,
        classify_network_error,
    )
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from downloader import (
        MODEL_CATALOG,
        get_bundle,
        resolve_bundle_paths,
        get_default_installed_bundle_id,
        get_catalog_summary,
        delete_bundle_files,
        download_kokoro_model,
        download_model_bundle,
        check_model_exists,
        get_available_models,
        classify_network_error,
    )

router = APIRouter()


def load_engine_logic(requested_model_id=None, requested_mode=None):
    global kokoro
    system_status["is_loading"] = True
    ort_gpu = has_onnxruntime_gpu()

    # 1. Determine model bundle to load
    if requested_model_id is None:
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            requested_model_id = settings.get("selected_model")
            if not requested_model_id:
                # Check legacy engine_mode mapping
                legacy_mode = settings.get("engine_mode")
                if legacy_mode == "cpu":
                    requested_model_id = "kokoro-v1.0-int8"
                else:
                    requested_model_id = "kokoro-v1.0"
        except Exception:
            requested_model_id = None

    if requested_model_id == "gpu":
        requested_model_id = "kokoro-v1.0"
    elif requested_model_id == "cpu":
        requested_model_id = "kokoro-v1.0-int8"

    if requested_model_id == "kokoro-v1.0-int8":
        int8_bundle = get_bundle("kokoro-v1.0-int8")
        if not int8_bundle or not int8_bundle.is_installed():
            print("[ENGINE] kokoro.int8.onnx not found. Falling back to Kokoro v1.0 (FP32) on CPU.")
            requested_model_id = "kokoro-v1.0"
            requested_mode = "cpu"

    if not requested_model_id:
        requested_model_id = get_default_installed_bundle_id()

    bundle = get_bundle(requested_model_id) if requested_model_id else None
    if not bundle or not bundle.is_installed():
        fallback_id = get_default_installed_bundle_id()
        if fallback_id:
            bundle = get_bundle(fallback_id)
            requested_model_id = fallback_id

    if not bundle or not bundle.is_installed():
        system_status["last_error"] = "No TTS voice models found. Please download a model."
        system_status["is_loading"] = False
        return

    # 2. Determine hardware execution mode (GPU vs CPU)
    if requested_mode is None:
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            requested_mode = settings.get("engine_mode", "gpu")
        except Exception:
            requested_mode = "gpu"

    if requested_mode == "gpu" and not ort_gpu:
        print("[ORT] onnxruntime-gpu not found (CPU package), skip GPU execution providers")
        requested_mode = "cpu"

    model_to_load = bundle.get_model_path()
    voices_path = bundle.get_voices_path()

    if not voices_path.exists():
        system_status["last_error"] = f"Voice pack missing for {bundle.name}. Please re-download."
        system_status["is_loading"] = False
        return

    actual_mode = requested_mode
    if not ort_gpu:
        actual_mode = "cpu"

    try:
        import app.state as state_module

        # Hardware Choke: Prevent C-level math libraries from spawning hidden threads
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
        os.environ["NUMEXPR_NUM_THREADS"] = "1"

        import onnxruntime as ort
        try:
            ort.set_default_logger_severity(3)
        except AttributeError:
            pass

        if state_module.kokoro is not None:
            print("[ENGINE] Unloading previous model...")
            state_module.kokoro = None
            gc.collect()

        if not ort_gpu:
            actual_mode = "cpu"
        elif actual_mode == "gpu":
            available_ort_providers = ort.get_available_providers()
            valid_gpus = [
                p for p in getattr(state_module, "providers", [])
                if p in available_ort_providers and p != "CPUExecutionProvider"
            ]
            if not valid_gpus:
                print(" -> [WARNING] No active GPU execution provider available. Fallback to CPU path.")
                actual_mode = "cpu"

        system_status["active_hardware"] = actual_mode
        system_status["active_model"] = bundle.id
        system_status["active_mode"] = requested_mode

        print(f"[ENGINE] Loading Model: {bundle.name} on Hardware: {actual_mode.upper()}...")
        print(f" -> Model Path: {model_to_load}")
        print(f" -> Voices Path: {voices_path}")

        if actual_mode == "gpu" and ort_gpu:
            available_ort_providers = ort.get_available_providers()
            custom_providers = []

            for p in getattr(state_module, "providers", []):
                if p in available_ort_providers:
                    if p == "CUDAExecutionProvider":
                        custom_providers.append((
                            "CUDAExecutionProvider",
                            {
                                "device_id": 0,
                                "cudnn_conv_algo_search": "DEFAULT",
                                "arena_extend_strategy": "kSameAsRequested",
                                "cudnn_conv_use_max_workspace": "0",
                                "do_copy_in_default_stream": "1",
                                "has_user_compute_stream": "0",
                            },
                        ))
                    else:
                        custom_providers.append(p)

            active_names = [
                p[0].replace("ExecutionProvider", "") if isinstance(p, tuple)
                else p.replace("ExecutionProvider", "")
                for p in custom_providers
            ]
            print(f" -> [ENGINE] Active Hardware Linked: \033[92m{' | '.join(active_names)}\033[0m")

            original_session = ort.InferenceSession

            def forced_gpu_session(*args, **kwargs):
                kwargs["providers"] = custom_providers
                if "sess_options" not in kwargs or kwargs["sess_options"] is None:
                    sess_options = ort.SessionOptions()
                    kwargs["sess_options"] = sess_options

                kwargs["sess_options"].enable_cpu_mem_arena = False
                kwargs["sess_options"].enable_mem_pattern = False
                kwargs["sess_options"].execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                kwargs["sess_options"].intra_op_num_threads = 1
                kwargs["sess_options"].inter_op_num_threads = 1
                kwargs["sess_options"].graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
                return original_session(*args, **kwargs)

            ort.InferenceSession = forced_gpu_session

            try:
                state_module.kokoro = PatchedKokoro(str(model_to_load), str(voices_path))
            except Exception as e:
                print(f"[ENGINE WARNING] PatchedKokoro failed: {e}. Trying standard Kokoro fallback.")
                ort.InferenceSession = original_session
                from kokoro_onnx import Kokoro
                state_module.kokoro = Kokoro(str(model_to_load), str(voices_path))
            finally:
                ort.InferenceSession = original_session

        else:
            print("[ENGINE] Loading Model in \033[92mRAM (CPU)\033[0m...")
            state_module.kokoro = PatchedKokoro(str(model_to_load), str(voices_path))
            state_module.kokoro_export = state_module.kokoro

        system_status["last_error"] = None
        print(f"[ENGINE] Successfully loaded {bundle.name} with {len(state_module.kokoro.get_voices())} voices.")

        # Validate and synchronize active voice for the loaded model
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                current_settings = json.load(f)
            cur_voice = current_settings.get("voice_id")
            avail_voices = state_module.kokoro.get_voices()
            if not cur_voice or cur_voice not in avail_voices:
                if "af_heart" in avail_voices:
                    new_voice = "af_heart"
                elif "af_maple" in avail_voices:
                    new_voice = "af_maple"
                elif "zf_001" in avail_voices:
                    new_voice = "zf_001"
                else:
                    new_voice = avail_voices[0] if avail_voices else "af_heart"
                current_settings["voice_id"] = new_voice
                current_settings["selected_model"] = bundle.id
                safe_save_json(settings_file, current_settings)
                print(f"[ENGINE] Synchronized active voice to '{new_voice}' for {bundle.name}.")
        except Exception as err:
            print(f"[ENGINE] Failed to sync voice settings: {err}")

    except Exception as e:
        system_status["last_error"] = f"Failed to load TTS engine: {str(e)}"
        print(f"[ENGINE ERROR] {system_status['last_error']}")
        import traceback
        traceback.print_exc()

    system_status["is_loading"] = False


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

    available_models = get_available_models()
    import app.state as state_module

    # Only expose an active_model when the engine is actually running in memory
    if state_module.kokoro is not None:
        active_model = system_status.get("active_model", selected_model)
        active_mode = system_status.get("active_mode", current_engine_mode)
    else:
        active_model = None
        active_mode = current_engine_mode

    return {
        "model_loaded": state_module.kokoro is not None,
        "is_loading": system_status["is_loading"],
        "is_downloading": system_status["is_downloading"],
        "downloading_model": system_status.get("downloading_model"),
        "download_progress": system_status.get("download_progress", 0),
        "download_stage": system_status.get("download_stage"),
        "last_error": system_status["last_error"],
        "voices": state_module.kokoro.get_voices() if state_module.kokoro else [],
        "engine_mode": current_engine_mode,
        "selected_model": selected_model,
        "active_model": active_model,
        "active_hardware": system_status.get("active_hardware", "cpu"),
        "available_models": available_models,
    }


# --- Model Management Endpoints ---
@router.get("/api/system/models")
async def list_models():
    import app.state as state_module

    # Only report an active model if the engine is actually running in memory.
    # On fresh/freeze start (kokoro is None), nothing is truly active.
    if state_module.kokoro is not None:
        active_id = system_status.get("active_model")
        if not active_id:
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                active_id = settings.get("selected_model", "kokoro-v1.0")
            except Exception:
                active_id = "kokoro-v1.0"
    else:
        active_id = None  # No engine → no model is "In Use"

    catalog = get_catalog_summary(active_id)
    return {
        "active_model": active_id,
        "is_loading": system_status["is_loading"],
        "is_downloading": system_status["is_downloading"],
        "downloading_model": system_status.get("downloading_model"),
        "download_progress": system_status.get("download_progress", 0),
        "download_stage": system_status.get("download_stage"),
        "last_error": system_status.get("last_error"),
        "active_hardware": system_status.get("active_hardware", "cpu"),
        "models": catalog,
    }


@router.post("/api/system/models/download")
async def download_model_endpoint(request: ModelDownloadRequest, background_tasks: BackgroundTasks):
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

            import app.state as state_module
            if state_module.kokoro is None:
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

    # Persist selected_model and valid default voice in settings.json
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
        settings["selected_model"] = model_id
        target_voice = "af_maple" if model_id == "kokoro-v1.1" else "af_heart"
        settings["voice_id"] = target_voice
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
        "default_voice": target_voice,
        "message": f"Switching to {bundle.name}...",
    }


@router.post("/api/system/models/delete")
async def delete_model_endpoint(request: ModelDeleteRequest):
    model_id = request.model_id
    bundle = get_bundle(model_id)
    if not bundle:
        raise HTTPException(status_code=400, detail=f"Unknown model ID: {model_id}")

    if system_status["is_downloading"] and system_status.get("downloading_model") == model_id:
        raise HTTPException(status_code=400, detail="Cannot delete while downloading")

    import app.state as state_module

    # Protect the currently active in-use model from deletion.
    # The UI hides the delete button, but this is the backend guard.
    is_engine_loaded = state_module.kokoro is not None
    is_active = is_engine_loaded and (system_status.get("active_model") == model_id)
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