from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from ..state import audio_cache, kokoro, system_status
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
        start_marvis_setup,       # <-- ADDED for Marvis
        load_marvis_into_memory   # <-- ADDED for Marvis
    )
    from logic.audio_cache import AudioCache

except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from downloader import (
        download_kokoro_model,
        check_model_exists,
        get_available_models,
        start_marvis_setup,       # <-- ADDED for Marvis
        load_marvis_into_memory   # <-- ADDED for Marvis
    )

router = APIRouter()

from ..state import PatchedKokoro

def load_engine_logic(requested_mode=None):
    global kokoro
    import app.state as state_module
    system_status["is_loading"] = True

    active_engine = "kokoro"
    if requested_mode is None:
        try:
            with open(settings_file, "r") as f:
                settings = json.load(f)
            requested_mode = settings.get("engine_mode", "gpu")
            active_engine = settings.get("active_engine", "kokoro") # <-- Get active engine
        except Exception:
            requested_mode = "gpu"

    # ==========================================
    # MARVIS ENGINE BOOT SEQUENCE
    # ==========================================
    if active_engine == "marvis":
        print(f"[ENGINE] Initializing Marvis TTS model...")
        try:
            if state_module.kokoro is not None:
                print("[ENGINE] Unloading Kokoro to free VRAM...")
                state_module.kokoro = None 
            
            load_marvis_into_memory(requested_mode)
            system_status["is_loading"] = False
            return
        except Exception as e:
            system_status["last_error"] = f"Failed to load Marvis: {str(e)}"
            print(f"[ENGINE ERROR] {system_status['last_error']}")
            system_status["is_loading"] = False
            return

    # ==========================================
    # KOKORO ENGINE BOOT SEQUENCE (Original Untouched Code)
    # ==========================================
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
        if state_module.kokoro is not None:
            print("[ENGINE] Unloading previous model...")
            state_module.kokoro = None  # GC old model
            
        # Also unload marvis if switching to kokoro
        if getattr(state_module, 'marvis_generator', None) is not None:
            state_module.marvis_generator = None

        print(f"[ENGINE] Initializing {actual_mode.upper()} model...")

        if actual_mode == "gpu":
            print("[ENGINE] Configuring strict CUDA GPU settings...")
            
            cuda_options = {
                "device_id": 0,                                 
                "cudnn_conv_algo_search": "HEURISTIC",         
            }
            custom_providers = [("CUDAExecutionProvider", cuda_options), "CPUExecutionProvider"]
            
            import onnxruntime as ort
            original_session = ort.InferenceSession
            
            def forced_gpu_session(*args, **kwargs):
                kwargs['providers'] = custom_providers
                if 'sess_options' not in kwargs or kwargs['sess_options'] is None:
                    sess_options = ort.SessionOptions()
                    kwargs['sess_options'] = sess_options
                kwargs['sess_options'].enable_mem_pattern = False 
                kwargs['sess_options'].graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
                return original_session(*args, **kwargs)
                
            ort.InferenceSession = forced_gpu_session
            
            try:
                state_module.kokoro = PatchedKokoro(str(model_to_load), str(voices_path))
            except Exception as e:
                print(f"[ENGINE WARNING] PatchedKokoro failed: {e}. Using standard Kokoro.")
                from kokoro_onnx import Kokoro
                state_module.kokoro = Kokoro(str(model_to_load), str(voices_path))
            finally:
                ort.InferenceSession = original_session
                
        else:
            print("[ENGINE] Using default Kokoro for CPU model...")
            from kokoro_onnx import Kokoro
            state_module.kokoro = Kokoro(str(model_to_load), str(voices_path))

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
    marvis_dir = models_dir / "marvis"
    
    available_models = {
        "gpu": (models_dir / "kokoro.onnx").exists(),
        "cpu": (models_dir / "kokoro.int8.onnx").exists(),
        "voices": (models_dir / "voices.bin").exists(),
        "marvis": (marvis_dir / "marvis_tts.pt").exists(), # <-- ADDED
    }

    import app.state as state_module

    marvis_loaded = getattr(state_module, 'marvis_model_loaded', False)

    return {
        "model_loaded": (state_module.kokoro is not None) or marvis_loaded, # <-- ADDED logic
        "is_loading": system_status["is_loading"],
        "is_downloading": system_status["is_downloading"],
        "last_error": system_status["last_error"],
        "voices": state_module.kokoro.get_voices() if state_module.kokoro else [],
        "engine_mode": current_engine_mode,
        "available_models": available_models,
    }


@router.post("/api/system/setup")
async def run_setup(background_tasks: BackgroundTasks, model_type: str = None, engine: str = "kokoro"): # <-- ADDED engine parameter
    if system_status["is_downloading"]:
        return {"status": "already_running"}

    if engine == "marvis":
        def marvis_setup_task():
            system_status["is_downloading"] = True
            system_status["last_error"] = None
            try:
                print(f"[SETUP] Starting download for Marvis model...")
                start_marvis_setup(model_type or "gpu")
                print("[SETUP] Marvis Setup complete!")
            except Exception as e:
                msg = f"Marvis setup failed: {str(e)}"
                system_status["last_error"] = msg
                print(f"[SETUP ERROR] {msg}")
            finally:
                system_status["is_downloading"] = False
        
        background_tasks.add_task(marvis_setup_task)
        return {"status": "started", "message": "Marvis setup started"}

    # Original Kokoro setup task
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
async def switch_engine(background_tasks: BackgroundTasks, target_mode: str, engine: str = "kokoro"):
    if target_mode not in ["gpu", "cpu"]:
        raise HTTPException(status_code=400, detail="Invalid engine mode")

    if system_status["is_downloading"]:
        return {"status": "busy", "message": "Cannot switch while downloading"}

    # Fix: Save both the target_mode (gpu/cpu) AND the active_engine (kokoro/marvis) instantly
    try:
        with open(settings_file, "r") as f:
            settings = json.load(f)
        settings["engine_mode"] = target_mode
        settings["active_engine"] = engine
        safe_save_json(settings_file, settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def reload_task():
        if system_status["is_loading"]:
            return
        try:
            load_engine_logic(target_mode)
        except Exception as e:
            system_status["last_error"] = str(e)

    background_tasks.add_task(reload_task)
    return {
        "status": "switching",
        "target_mode": target_mode,
        "engine": engine,
        "message": f"Switching to {engine}...",
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