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

# Extract dynamic ONNX providers configured in main.py, filtering out empty strings
ort_env = os.environ.get("ORT_AUTO_PROVIDERS", "")
state_module.providers = [p for p in ort_env.split(",") if p]

from .models import AppSettings

from .routers import settings, library, tts, system, export, timer, theme, render, view

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

    # 🌟 SURGICAL FIX: Use models.py as the absolute source of truth for defaults
    try:
        current_data = {}
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                current_data = json.load(f)
                
        # Satisfy mandatory Pydantic fields
        if "pronunciationRules" not in current_data: current_data["pronunciationRules"] = []
        if "ignoreList" not in current_data: current_data["ignoreList"] = []
            
        # Pydantic safely merges user data with models.py defaults
        merged_settings = AppSettings(**current_data)
        safe_save_json(settings_file, merged_settings.model_dump())
    except Exception:
        # Fallback if file is completely corrupted: Generate fresh from models.py
        fallback_settings = AppSettings(pronunciationRules=[], ignoreList=[])
        safe_save_json(settings_file, fallback_settings.model_dump())

    safe_init_json(library_file, [])

    from .routers.system import load_engine_logic
    from .state import system_status

    def perform_boot():
        try:
            print("[BOOT] Loading Kokoro Engine in background...")
            load_engine_logic()
            print(f"[BOOT] Kokoro Engine loaded in {time.time() - start_time:.2f}s")
        except Exception as e:
            print(f"[WARNING] Engine load bypassed (missing models or error): {e}")
            system_status["is_loading"] = False

    # Do not block FastAPI/window on model RAM load. Status dot stays yellow then green/red.
    system_status["is_loading"] = True
    threading.Thread(target=perform_boot, daemon=True, name="kokoro-boot").start()

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
app.include_router(render.router)
app.include_router(view.router)

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