import os
import shutil
from fastapi import APIRouter, BackgroundTasks
from huggingface_hub import snapshot_download

router = APIRouter(prefix="/api/lavasr", tags=["LavaSR"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
LAVASR_DIR = os.path.abspath(os.path.join(MODELS_DIR, "LavaSR"))

download_state = {
    "is_downloading": False,
    "progress": 0,
    "status_msg": "",
    "error": None
}

@router.get("/status")
def get_status():
    # STRICT FIX: Must have both the heavy weight file AND the config file
    target_bin = os.path.join(LAVASR_DIR, "enhancer_v2", "pytorch_model.bin")
    target_yaml = os.path.join(LAVASR_DIR, "enhancer_v2", "config.yaml")
    
    exists = (os.path.exists(target_bin) and os.path.getsize(target_bin) > 1000000) and os.path.exists(target_yaml)
    
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
        print(f"[LavaSR] Starting brute-force download sequence...")
        
        download_state["status_msg"] = "Fetching files from HuggingFace..."
        download_state["progress"] = 20
        
        cache_dir = snapshot_download(repo_id="YatharthS/LavaSR")
        
        download_state["status_msg"] = "Extracting files to models directory..."
        download_state["progress"] = 70
        print(f"[LavaSR] Downloaded to temporary cache: {cache_dir}")
        print(f"[LavaSR] Moving files to local folder asset: {LAVASR_DIR}")
        
        shutil.copytree(cache_dir, LAVASR_DIR, dirs_exist_ok=True)
        
        download_state["progress"] = 100
        download_state["status_msg"] = "Complete!"
        print(f"[LavaSR] Success! All files placed inside {LAVASR_DIR}")
        
    except Exception as e:
        download_state["error"] = str(e)
        print(f"[LavaSR] Download processing error encountered: {e}")
    finally:
        download_state["is_downloading"] = False

@router.post("/download")
def start_download(background_tasks: BackgroundTasks):
    if not download_state["is_downloading"]:
        background_tasks.add_task(do_download)
    return {"status": "started"}