from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from ..state import audio_cache, kokoro, system_status, PatchedKokoro
from ..utils import safe_save_json
from ..config import base_dir, settings_file, get_app_anchored_path
import json
import sys
from pathlib import Path

# Add app logic to path for imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

try:
    from logic.downloader import (
        download_kokoro_model,
        check_model_exists,
        get_available_models,
    )
    from logic.audio_cache import AudioCache
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from downloader import (
        download_kokoro_model,
        check_model_exists,
        get_available_models,
    )

router = APIRouter()

def load_engine_logic(requested_mode=None):
    global kokoro
    system_status["is_loading"] = True

    if requested_mode is None:
        try:
            with open(settings_file, "r") as f:
                settings = json.load(f)
            requested_mode = settings.get("engine_mode", "gpu")
        except Exception:
            requested_mode = "gpu"

    models_dir = base_dir / "models"
    voices_path = models_dir / "voices.bin"
    gpu_model_path = models_dir / "kokoro.onnx"
    cpu_model_path = models_dir / "kokoro.int8.onnx"

    if requested_mode == "cpu":
        primary_model = cpu_model_path
        fallback_model = gpu_model_path
        primary_label = "CPU (Quantized)"
        fallback_label = "GPU (Standard)"
    else:
        primary_model = gpu_model_path
        fallback_model = cpu_model_path
        primary_label = "GPU (Standard)"
        fallback_label = "CPU (Quantized)"

    if not voices_path.exists():
        system_status["last_error"] = "Voice pack missing. Please run setup."
        system_status["is_loading"] = False
        return

    model_to_load = None
    actual_mode = requested_mode

    if primary_model.exists():
        model_to_load = primary_model
        print(f"[ENGINE] Loading {primary_label} model: {primary_model.name}")
    elif fallback_model.exists():
        model_to_load = fallback_model
        actual_mode = "cpu" if requested_mode == "gpu" else "gpu"
        msg = f"Requested {primary_label} model not found. Using {fallback_label} model instead."
        print(f"[ENGINE] {msg}")
        system_status["last_error"] = msg
    else:
        system_status["last_error"] = "No TTS models found. Please run setup."
        system_status["is_loading"] = False
        return

    try:
        import app.state as state_module
        import os
        
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
            state_module.kokoro = None  # GC old model

        # Clean fallback check: Route to pure CPU branch if no valid GPU providers exist
        if actual_mode == "gpu":
            available_ort_providers = ort.get_available_providers()
            valid_gpus = [p for p in getattr(state_module, "providers", []) if p in available_ort_providers and p != "CPUExecutionProvider"]
            if not valid_gpus:
                print(" -> [WARNING] No active GPU execution provider available. Fallback to CPU path.")
                actual_mode = "cpu"

        print(f"[ENGINE] Initializing {actual_mode.upper()} model...")

        if actual_mode == "gpu":
            available_ort_providers = ort.get_available_providers()
            custom_providers = []
            
            for p in getattr(state_module, "providers", []):
                if p in available_ort_providers:
                    if p == "CUDAExecutionProvider":
                        # Set HEURISTIC for minimal VRAM overhead and fast initialization
                        custom_providers.append(("CUDAExecutionProvider", {
                            "device_id": 0, 
                            "cudnn_conv_algo_search": "HEURISTIC",
                            "arena_extend_strategy": "kSameAsRequested"
                        }))
                    else:
                        custom_providers.append(p)
            
            active_names = [p[0].replace("ExecutionProvider", "") if isinstance(p, tuple) else p.replace("ExecutionProvider", "") for p in custom_providers]
            print(f" -> [ENGINE] Active Hardware Linked: \033[92m{' | '.join(active_names)}\033[0m")
            
            original_session = ort.InferenceSession
            
            def forced_gpu_session(*args, **kwargs):
                    kwargs['providers'] = custom_providers
                    
                    if 'sess_options' not in kwargs or kwargs['sess_options'] is None:
                        sess_options = ort.SessionOptions()
                        kwargs['sess_options'] = sess_options
                    
                    # Prevent MEMORY LEAK 
                    kwargs['sess_options'].enable_cpu_mem_arena = False
                    kwargs['sess_options'].enable_mem_pattern = False 
                    
                    kwargs['sess_options'].graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
                    
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
            
            # Use PatchedKokoro to enforce strict float32/int64 tensor types and prevent ONNX mapping crashes
            state_module.kokoro = PatchedKokoro(str(model_to_load), str(voices_path))
            state_module.kokoro_export = state_module.kokoro # Bind export to same instance for CPU

    except Exception as e:
        system_status["last_error"] = f"Failed to load TTS engine: {str(e)}"
        print(f"[ENGINE ERROR] {system_status['last_error']}")
        import traceback
        traceback.print_exc()

    system_status["is_loading"] = False


@router.get("/api/system/status")
async def get_status():
    try:
        with open(settings_file, "r") as f:
            settings = json.load(f)
        current_engine_mode = settings.get("engine_mode", "gpu")
    except Exception:
        current_engine_mode = "gpu"

    models_dir = base_dir / "models"
    available_models = {
        "gpu": (models_dir / "kokoro.onnx").exists(),
        "cpu": (models_dir / "kokoro.int8.onnx").exists(),
        "voices": (models_dir / "voices.bin").exists(),
    }

    import app.state as state_module

    return {
        "model_loaded": state_module.kokoro is not None,
        "is_loading": system_status["is_loading"],
        "is_downloading": system_status["is_downloading"],
        "last_error": system_status["last_error"],
        "voices": state_module.kokoro.get_voices() if state_module.kokoro else [],
        "engine_mode": current_engine_mode,
        "available_models": available_models,
    }


@router.post("/api/system/setup")
async def run_setup(background_tasks: BackgroundTasks, model_type: str = None):
    if system_status["is_downloading"]:
        return {"status": "already_running"}

    def setup_task():
        system_status["is_downloading"] = True
        system_status["last_error"] = None
        try:
            target_model = model_type
            if target_model is None:
                try:
                    with open(settings_file, "r") as f:
                        settings = json.load(f)
                    target_model = settings.get("engine_mode", "gpu")
                except:
                    target_model = "gpu"

            if target_model not in ["gpu", "cpu"]:
                target_model = "gpu"

            print(f"[SETUP] Starting download for {target_model} model...")
            download_kokoro_model(target_model)
            print("[SETUP] Download complete, loading engine...")
            load_engine_logic(target_model)
            print("[SETUP] Setup complete!")
        except Exception as e:
            msg = f"Setup failed: {str(e)}"
            system_status["last_error"] = msg
            print(f"[SETUP ERROR] {msg}")
        finally:
            system_status["is_downloading"] = False

    background_tasks.add_task(setup_task)
    return {"status": "started"}


@router.post("/api/system/switch-engine")
async def switch_engine(background_tasks: BackgroundTasks, target_mode: str):
    if target_mode not in ["gpu", "cpu"]:
        raise HTTPException(status_code=400, detail="Invalid engine mode")

    if system_status["is_downloading"]:
        return {"status": "busy", "message": "Cannot switch while downloading"}

    models_dir = base_dir / "models"
    target_path = models_dir / (
        "kokoro.onnx" if target_mode == "gpu" else "kokoro.int8.onnx"
    )

    if not target_path.exists():
        return {
            "status": "model_missing",
            "message": f"{target_mode.upper()} model not downloaded.",
            "requires_download": True,
        }

    try:
        with open(settings_file, "r") as f:
            settings = json.load(f)
        settings["engine_mode"] = target_mode
        safe_save_json(settings_file, settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def reload_task():
        if system_status["is_loading"]:
            return
        system_status["is_loading"] = True
        try:
            load_engine_logic(target_mode)
        except Exception as e:
            system_status["last_error"] = str(e)
        finally:
            system_status["is_loading"] = False

    background_tasks.add_task(reload_task)
    return {
        "status": "switching",
        "target_mode": target_mode,
        "message": f"Switching to {target_mode}...",
    }


@router.post("/api/system/download-model")
async def download_specific_model(background_tasks: BackgroundTasks, model_type: str):
    if model_type not in ["gpu", "cpu"]:
        raise HTTPException(status_code=400, detail="Invalid model type")

    if system_status["is_downloading"]:
        return {"status": "already_downloading"}

    models_dir = base_dir / "models"
    path = models_dir / ("kokoro.onnx" if model_type == "gpu" else "kokoro.int8.onnx")

    if path.exists():
        return {"status": "already_exists", "message": "Model already downloaded"}

    def download_task():
        system_status["is_downloading"] = True
        try:
            download_kokoro_model(model_type)
        except Exception as e:
            system_status["last_error"] = str(e)
        finally:
            system_status["is_downloading"] = False

    background_tasks.add_task(download_task)
    return {"status": "started"}


@router.post("/api/system/clear-cache")
async def clear_all_cache():
    try:
        deleted, freed = audio_cache.clear_all()
        return {
            "status": "success",
            "files_deleted": deleted,
            "freed_mb": round(freed, 2),
            "message": f"Cleared {deleted} entries, freed {freed:.1f} MB",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))