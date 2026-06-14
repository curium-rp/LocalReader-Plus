import os
from fastapi import APIRouter

router = APIRouter(prefix="/api/lavasr", tags=["LavaSR"])

# Calculate all possible root directories to search
CURRENT_FILE = os.path.abspath(__file__)
APP_DIR = os.path.dirname(os.path.dirname(CURRENT_FILE))
DIST_DIR = os.path.dirname(APP_DIR)
PROJECT_ROOT = os.path.dirname(DIST_DIR)

SEARCH_DIRS = [
    os.path.join(DIST_DIR, "models"),
    os.path.join(PROJECT_ROOT, "models"),
    DIST_DIR,
    PROJECT_ROOT
]

@router.get("/status")
def get_status():
    has_code = False
    has_weights = False
    
    for search_dir in SEARCH_DIRS:
        if not os.path.exists(search_dir):
            continue
            
        for root, dirs, files in os.walk(search_dir):
            lower_files = [f.lower() for f in files]
            current_folder = os.path.basename(root).lower()
            
            # Look for the code
            if not has_code and "model.py" in lower_files and current_folder == "lavasr":
                has_code = True
                
            # Look for the weights root folder containing enhancer_v2 and denoiser
            if not has_weights and "enhancer_v2" in [d.lower() for d in dirs] and "denoiser" in [d.lower() for d in dirs]:
                bin_path = os.path.join(root, "enhancer_v2", "pytorch_model.bin")
                yaml_path = os.path.join(root, "enhancer_v2", "config.yaml")
                denoiser_path = os.path.join(root, "denoiser", "denoiser.bin")
                
                # The Ghost Disconnect Fix: Sync UI status strictly with PyTorch requirements
                if os.path.exists(bin_path) and os.path.exists(yaml_path) and os.path.exists(denoiser_path):
                    if os.path.getsize(bin_path) > 1000000:
                        has_weights = True
                        
        if has_code and has_weights:
            break
            
    exists = has_code and has_weights
    
    return {
        "exists": exists,
        "is_downloading": False,
        "progress": 100 if exists else 0,
        "status_msg": "Active" if exists else "Not Installed",
        "error": None
    }

@router.post("/download")
def start_download():
    return {"status": "manual_install_required"}