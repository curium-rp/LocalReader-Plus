import os
import sys
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path

# ==========================================
# 1. PATH RESOLUTION
# ==========================================
base_dir = Path(__file__).parent
base_dir_parent = base_dir.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

# ==========================================
# 2. ROUTER IMPORTS
# ==========================================
from .routers import settings, library, tts, system, export, timer, theme, f5_manager, fish_manager  
# ==========================================
# 3. NON-BLOCKING BOOT SEQUENCE
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs when the server starts. We offload the heavy model loading 
    to a background thread so it doesn't overlap or block the UI 
    from being served instantly.
    """
    from .routers.system import load_engine_logic
    
    print("[SERVER] FastAPI is up! Initiating background engine boot...")
    
    # Start the model loader in a daemon thread so it runs invisibly
    boot_thread = threading.Thread(target=load_engine_logic, daemon=True)
    boot_thread.start()
    
    yield # The server runs and serves requests here
    
    print("[SERVER] Shutting down gracefully...")

# ==========================================
# 4. FASTAPI APP INITIALIZATION
# ==========================================
app = FastAPI(title="LocalReader-Plus API", lifespan=lifespan)

# CORS Setup to prevent frontend/backend port overlap issues
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 5. REGISTER API ROUTERS
# (Must be registered BEFORE the static UI mount)
# ==========================================
app.include_router(settings.router)
app.include_router(library.router)
app.include_router(tts.router)
app.include_router(system.router)
app.include_router(export.router)
app.include_router(timer.router)
app.include_router(theme.router)
app.include_router(f5_manager.router)
app.include_router(fish_manager.router)
# ==========================================
# 6. MOUNT FRONTEND UI
# ==========================================
ui_dir = base_dir / "ui"

if ui_dir.exists():
    # Serves the index.html and all JS/CSS files
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
else:
    print(f"[SERVER WARNING] UI directory not found at {ui_dir}! The GUI will not load.")

# Safety fallback if someone hits a raw endpoint
@app.get("/")
async def root():
    return RedirectResponse(url="/index.html")