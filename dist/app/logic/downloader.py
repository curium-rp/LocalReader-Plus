import os
import shutil
import requests
from huggingface_hub import hf_hub_download
from typing import Literal

def download_kokoro_model(model_type: Literal["gpu", "cpu"] = "gpu") -> None:
    """
    Download the specified TTS model (Standard or Quantized).
    
    Args:
        model_type: "gpu" (standard FP32, ~309MB) or "cpu" (quantized Int8, ~87MB)
    """
    # Target directory: backend/models/
    target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    os.makedirs(target_dir, exist_ok=True)

    print(f"--- LocalReader Downloader (Dual-Engine) ---")
    print(f"Target: {target_dir}")
    print(f"Mode: {model_type.upper()}\n")

    # Determine model configuration
    if model_type == "cpu":
        # v2.0: Use v1.0 quantized model for multilingual support (FR/ES/JP)
        model_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
        model_dest = os.path.join(target_dir, "kokoro.int8.onnx")
        model_label = "Quantized CPU Model (Int8 - Multilingual)"
        model_size = "~87MB"
    else:  # "gpu"
        model_repo = "onnx-community/Kokoro-82M-v1.0-ONNX"
        model_remote_path = "onnx/model.onnx"
        model_dest = os.path.join(target_dir, "kokoro.onnx")
        model_label = "Standard GPU Model (FP32)"
        model_size = "~309MB"

    # Download Model
    if not os.path.exists(model_dest):
        print(f"Downloading {model_label} ({model_size})...")
        try:
            if model_type == "cpu":
                # Direct download from GitHub releases
                print(f"  Starting download from: {model_url}")
                r = requests.get(model_url, stream=True, timeout=600)  # 10 min timeout for large file
                r.raise_for_status()
                
                total_size = int(r.headers.get('content-length', 0))
                total_size_mb = total_size / (1024 * 1024) if total_size > 0 else 0
                downloaded = 0
                
                print(f"  Total size: {total_size_mb:.1f} MB")
                
                with open(model_dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            # Progress indicator
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                downloaded_mb = downloaded / (1024 * 1024)
                                print(f"  Progress: {progress:.1f}% ({downloaded_mb:.1f}/{total_size_mb:.1f} MB)", end='\r')
                
                print(f"\n  [OK] {model_label} saved as kokoro.int8.onnx")
            else:
                # HuggingFace download for GPU model
                path = hf_hub_download(repo_id=model_repo, filename=model_remote_path, local_dir=target_dir)
                
                # hf_hub_download with local_dir might put it in target_dir/onnx/model.onnx
                downloaded_file = os.path.join(target_dir, "onnx", "model.onnx")
                if os.path.exists(downloaded_file):
                    shutil.move(downloaded_file, model_dest)
                    print(f"{model_label} saved as kokoro.onnx")
                elif os.path.exists(path) and path != model_dest:
                    shutil.copy2(path, model_dest)
                    print(f"{model_label} saved as kokoro.onnx")
        except Exception as e:
            print(f"Model download failed: {e}")
            raise
    else:
        print(f"{model_label} already exists.")

    # Download Voices (Shared resource - only download if missing)
    # MULTILINGUAL MODEL: voices-v1.0.bin (~30MB with FR/ES/JP support)
    voices_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    voices_dest = os.path.join(target_dir, "voices.bin")
    
    if not os.path.exists(voices_dest):
        print(f"\nDownloading Voice Pack (shared resource)...")
        try:
            r = requests.get(voices_url, stream=True, timeout=60)
            r.raise_for_status()
            
            with open(voices_dest, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            print("Voice Pack saved as voices.bin")
            
            # Remove old voices.json to avoid confusion
            old_json = os.path.join(target_dir, "voices.json")
            if os.path.exists(old_json):
                os.remove(old_json)
        except Exception as e:
            print(f"Voice Pack download failed: {e}")
            raise
    else:
        print("Voice Pack already exists (shared between both engines).")

    # Final Cleanup
    onnx_folder = os.path.join(target_dir, "onnx")
    if os.path.exists(onnx_folder):
        shutil.rmtree(onnx_folder)
    
    print(f"\nDownload complete! Active mode: {model_type.upper()}")
    print(f"Run 'python main.py' to start.")

def check_model_exists(model_type: Literal["gpu", "cpu"]) -> bool:
    """Check if a specific model type exists."""
    target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    
    if model_type == "cpu":
        model_path = os.path.join(target_dir, "kokoro.int8.onnx")
    else:
        model_path = os.path.join(target_dir, "kokoro.onnx")
    
    return os.path.exists(model_path)

def get_available_models() -> dict:
    """Return which models are currently downloaded."""
    return {
        "gpu": check_model_exists("gpu"),
        "cpu": check_model_exists("cpu"),
        "voices": os.path.exists(os.path.join(os.path.dirname(__file__), "..", "models", "voices.bin"))
    }

if __name__ == "__main__":
    import sys
    model_type = sys.argv[1] if len(sys.argv) > 1 else "gpu"
    if model_type not in ["gpu", "cpu"]:
        print("Usage: python downloader.py [gpu|cpu]")
        print("  gpu: Standard model (~309MB, best quality)")
        print("  cpu: Quantized model (~87MB, faster, low RAM)")
        sys.exit(1)
    download_kokoro_model(model_type)

import os
import requests
from pathlib import Path
import torch
import app.state as state_module

# Define our clean folder structure
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MARVIS_MODEL_DIR = BASE_DIR / "models" / "marvis"
VOICES_DIR = BASE_DIR / "voices" / "marvis"

def download_file_with_progress(url: str, dest_path: Path, description: str):
    """Helper utility to safely download files with a stream buffer."""
    if dest_path.exists():
        print(f"[Marvis Setup] {description} already exists. Skipping download.")
        return

    print(f"[Marvis Setup] Downloading {description}...")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    response = requests.get(url, stream=True)
    response.raise_for_status()
    
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    print(f"[Marvis Setup] Finished downloading {description}.")

# ==========================================
# MARVIS DOWNLOAD & BOOT SYSTEM
# ==========================================
def start_marvis_setup(model_type="gpu"):
    import app.config as config_module
    import app.state as state_module
    
    base_dir = config_module.base_dir
    print("[SETUP] Initiating Marvis Engine Download Sequence...")
    
    marvis_dir = base_dir / "models" / "marvis"
    voices_dir = base_dir / "voices" / "marvis"
    
    marvis_dir.mkdir(parents=True, exist_ok=True)
    voices_dir.mkdir(parents=True, exist_ok=True)
    
    # We use the official huggingface_hub library to safely find the right files
    try:
        from huggingface_hub import snapshot_download
        print(f"[SETUP] Connecting to HuggingFace to download official Marvis weights...")
        
        # Download the base PyTorch repo (fallback to main repo if needed)
        try:
            snapshot_download(
                repo_id="Marvis-AI/marvis-tts-250m-v0.1-base-pt",
                local_dir=str(marvis_dir),
                local_dir_use_symlinks=False
            )
        except Exception as e:
            print(f"[SETUP] Base-PT repo failed ({e}). Falling back to main repository...")
            snapshot_download(
                repo_id="Marvis-AI/marvis-tts-250m-v0.1",
                local_dir=str(marvis_dir),
                local_dir_use_symlinks=False
            )
        print("[SETUP] Marvis weights downloaded successfully.")
        
    except ImportError:
        print("[SETUP ERROR] 'huggingface_hub' is missing! Please run: pip install huggingface_hub")
        return
    except Exception as e:
        print(f"[SETUP ERROR] Failed to download Marvis weights: {e}")
        return
            
    # Create the Default "Template" Cloned Voice Folder
    dummy_voice = voices_dir / "default"
    dummy_voice.mkdir(exist_ok=True)
    if not (dummy_voice / "ref.txt").exists():
        with open(dummy_voice / "ref.txt", "w", encoding="utf-8") as f:
            f.write("This is a default reference text for voice cloning.")
            
    # Automatically load the model into memory
    print("[SETUP] Files verified. Triggering Marvis Memory Load...")
    load_marvis_into_memory(model_type)


def load_marvis_into_memory(model_type="gpu"):
    import torch
    import app.config as config_module
    import app.state as state_module
    from pathlib import Path
    
    base_dir = config_module.base_dir
    print("[ENGINE] Loading Marvis into memory...")
    marvis_dir = base_dir / "models" / "marvis"
    
    # Auto-detect the correct weights file (.pt or .safetensors)
    ckpt_path = None
    for ext in ["*.pt", "*.pth", "*.safetensors", "model.safetensors", "pytorch_model.bin"]:
        found = list(marvis_dir.rglob(ext))
        if found:
            ckpt_path = found[0]
            break
            
    if not ckpt_path:
        print("[ENGINE ERROR] No Marvis weights (.pt or .safetensors) found in the models/marvis directory. Setup aborted.")
        return
        
    print(f"[ENGINE] Found weights file: {ckpt_path.name}")
    
    device = "cuda" if model_type == "gpu" and torch.cuda.is_available() else "cpu"
    
    try:
        # Load the raw architecture from the marvis-tts repository code
        from marvis_tts.generator import Generator
        from marvis_tts.models import ModelArgs, Model
        from marvis_tts.utils import load_smollm2_tokenizer
        
        state_module.marvis_tokenizer = load_smollm2_tokenizer()
        
        model_args = ModelArgs(
            backbone_flavor="llama-250M",
            decoder_flavor="llama-60M",
            text_vocab_size=state_module.marvis_tokenizer.vocab_size,
            audio_vocab_size=2051,
            audio_num_codebooks=32,
        )
        
        raw_model = Model(model_args).to(device=device, dtype=torch.float32)
        
        # Safely load either safetensors or torch pt formats
        if ckpt_path.suffix == ".safetensors":
            from safetensors.torch import load_file
            state_dict = load_file(ckpt_path)
            # Clean up keys if HuggingFace added a 'model.' prefix
            if any(k.startswith("model.") for k in state_dict.keys()):
                 state_dict = {k.replace("model.", ""): v for k, v in state_dict.items()}
        else:
            obj = torch.load(str(ckpt_path), weights_only=True, map_location="cpu")
            state_dict = obj["model_state"] if isinstance(obj, dict) and "model_state" in obj else obj
            
        raw_model.load_state_dict(state_dict, strict=False)
        raw_model.eval()
        
        # Bind it to the global state
        state_module.marvis_generator = Generator(raw_model, text_tokenizer=state_module.marvis_tokenizer, device=device)
        state_module.marvis_model_loaded = True
        
        print(f"[ENGINE] Marvis Engine fully online and ready on {device.upper()}!")
        
    except ImportError as e:
        print(f"[ENGINE ERROR] Missing Python Module: {e}")
        print("ACTION REQUIRED: Ensure 'safetensors' and 'marvis_tts' are installed.")
        state_module.marvis_model_loaded = False
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[ENGINE ERROR] Failed to load Marvis: {e}")
        state_module.marvis_model_loaded = False