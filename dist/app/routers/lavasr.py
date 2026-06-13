import os
from fastapi import APIRouter, BackgroundTasks
from huggingface_hub import snapshot_download

router = APIRouter(prefix="/api/lavasr", tags=["LavaSR"])

# Target directory: dist/models/LavaSR
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
LAVASR_DIR = os.path.join(MODELS_DIR, "LavaSR")

# In-memory state for the UI progress bar
download_state = {
    "is_downloading": False,
    "progress": 0,
    "status_msg": "",
    "error": None
}

@router.get("/status")
def get_status():
    # Check if the core weights and code exist in our custom folder
    exists = os.path.exists(os.path.join(LAVASR_DIR, "enhancer_v2", "generator.bin"))
    
    return {
        "exists": exists and not download_state["is_downloading"],
        "is_downloading": download_state["is_downloading"],
        "progress": download_state["progress"],
        "status_msg": download_state["status_msg"],
        "error": download_state["error"]
    }

def do_download():
    download_state["is_downloading"] = True
    download_state["error"] = None
    
    try:
        os.makedirs(LAVASR_DIR, exist_ok=True)
        download_state["status_msg"] = "Downloading LavaSR code and weights..."
        download_state["progress"] = 30
        
        # Download EVERYTHING directly into our local folder, skipping the global cache
        snapshot_download(
            repo_id="YatharthS/LavaSR",
            local_dir=LAVASR_DIR,
            local_dir_use_symlinks=False
        )
        
        download_state["progress"] = 100
        download_state["status_msg"] = "Complete!"
    except Exception as e:
        download_state["error"] = str(e)
    finally:
        download_state["is_downloading"] = False

@router.post("/download")
def start_download(background_tasks: BackgroundTasks):
    if not download_state["is_downloading"]:
        background_tasks.add_task(do_download)
    return {"status": "started"}