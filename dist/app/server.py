import os
import sys
import json
import time
import threading
import platform
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .config import (
    base_dir,
    userdata_dir,
    content_dir,
    settings_file,
    library_file,
)
from .utils import safe_save_json, safe_init_json
import app.state as state_module

from .routers import settings, library, tts, system, export, timer, theme, lavasr

# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()

    if not base_dir.exists():
        print(f"[CRITICAL] Base dir missing: {base_dir}")
    try:
        if content_dir.exists():
            for f in content_dir.glob("temp_*"):
                try: f.unlink()
                except: pass
    except Exception:
        pass

    is_first_run = not settings_file.exists()

    # Changed default wait_engine_load to 0 (Instant pop-up)
    safe_init_json(
        settings_file,
        {
            "pronunciationRules": [],
            "ignoreList": [],
            "voice_id": "af_bella",
            "speed": 1.0,
            "engine_mode": "gpu",
            "ui_language": "en",
            "auto_load_engine": 1,
            "wait_engine_load": 0, 
            "upscaler_active": False 
        },
    )
    safe_init_json(library_file, [])

    current_settings = {}
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            current_settings = json.load(f)
    except Exception:
        pass

    from .routers.system import load_engine_logic
    
    def perform_boot():
        try:
            print("[BOOT] Loading Kokoro Engine...")
            load_engine_logic()
            print(f"[BOOT] Kokoro Engine loaded in {time.time() - start_time:.2f}s")
            
            if current_settings.get("upscaler_active", False):
                print("[BOOT] Upscaler active. Attempting to load LavaSR...")
                try:
                    if hasattr(lavasr, 'load_upscaler_logic'):
                        lavasr.load_upscaler_logic()
                except Exception as e:
                    print(f"[WARNING] Upscaler load failed: {e}. Skipping upscaler to prevent crash.")
                    
        except Exception as e:
            print(f"[ERROR] System Boot failed: {e}")

    # The Core Logic Matrix
    if is_first_run:
        print("[SERVER] First launch detected. Skipping model load. App opens instantly.")
    elif current_settings.get("auto_load_engine", 1) == 0:
        print("[SERVER] auto_load_engine is 0. Waiting for user to manually load from UI.")
    else:
        if current_settings.get("wait_engine_load", 0) == 1:
            print("[SERVER] wait_engine_load is 1. Server will block until engines load (UI shows black screen)...")
            perform_boot()
        else:
            print("[SERVER] wait_engine_load is 0. Loading engines in background thread. UI pops up instantly!")
            threading.Thread(target=perform_boot, daemon=True).start()

    yield

    print("[SHUTDOWN] Cleanup complete.")
    try:
        state_module.sleep_timer.stop_timer()
    except Exception:
        pass

# --- App Definition ---
app = FastAPI(title="LocalReader Plus", lifespan=lifespan)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(settings.router)
app.include_router(library.router)
app.include_router(tts.router)
app.include_router(system.router)
app.include_router(export.router)
app.include_router(timer.router)
app.include_router(theme.router)
app.include_router(lavasr.router)

# --- Static Files ---
ui_dir = base_dir / "ui"
if ui_dir.exists():
    app.mount("/css", StaticFiles(directory=ui_dir / "css"), name="css")
    app.mount("/js", StaticFiles(directory=ui_dir / "js"), name="js")
    if (ui_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")
    app.mount("/locales", StaticFiles(directory=base_dir / "locales"), name="locales")
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
else:
    print(f"[WARNING] UI directory not found: {ui_dir}")

# --- Root Endpoints ---
@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/")
async def root():
    return RedirectResponse(url="/index.html")