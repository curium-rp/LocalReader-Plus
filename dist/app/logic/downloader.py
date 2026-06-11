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

def start_marvis_setup(model_type: str = "gpu"):
    """Main background task runner to handle downloads and loading sequence."""
    try:
        state_module.is_marvis_downloading = True
        
        # Ensure folders exist
        MARVIS_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        VOICES_DIR.mkdir(parents=True, exist_ok=True)

        # 1. URLs for Marvis Weights (CSM-1B & Mimi Codec wrapped weights)
        # (Replace these strings with the direct repository download links when available)
        MODEL_URL = "https://huggingface.co/marvis-labs/marvis-tts/resolve/main/marvis_tts.pt"
        
        checkpoint_path = MARVIS_MODEL_DIR / "marvis_tts.pt"
        
        # Download core model weights
        download_file_with_progress(MODEL_URL, checkpoint_path, "Marvis TTS Checkpoint")

        # Create a default fallback voice template so the app doesn't crash on initial run
        default_voice_dir = VOICES_DIR / "default_voice"
        default_voice_dir.mkdir(parents=True, exist_ok=True)
        
        ref_audio = default_voice_dir / "ref.wav"
        ref_text = default_voice_dir / "ref.txt"
        
        if not ref_text.exists():
            with open(ref_text, "w", encoding="utf-8") as f:
                f.write("This is a default sample reference text for voice cloning.")

        state_module.is_marvis_downloading = False
        
        # 2. Trigger Model Loading Sequence immediately after download finishes
        load_marvis_into_memory(model_type)

    except Exception as e:
        print(f"[Marvis Setup Error] Setup failed: {e}")
        state_module.is_marvis_downloading = False
        state_module.is_marvis_loading = False

def load_marvis_into_memory(model_type: str = "gpu"):
    """Loads the downloaded model weights cleanly into RAM or VRAM."""
    try:
        state_module.is_marvis_loading = True
        print("[Marvis Load] Initializing Marvis TTS model layers...")

        # Determine compute target device mapping
        if model_type == "gpu" and torch.cuda.is_available():
            device = "cuda"
        elif model_type == "gpu" and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

        checkpoint_path = MARVIS_MODEL_DIR / "marvis_tts.pt"
        
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Missing weight files at {checkpoint_path}")

        # Import modules dynamically from the marvis-tts repository code
        from marvis_tts.generator import MarvisGenerator
        from transformers import AutoTokenizer

        # Load Tokenizer & Model Generator
        print(f"[Marvis Load] Loading weights onto target device: {device}")
        state_module.marvis_tokenizer = AutoTokenizer.from_pretrained("marvis-labs/marvis-tts")
        
        # Initialize generator layer directly using the weights matching inference.py structure
        state_module.marvis_generator = MarvisGenerator.from_pretrained(str(checkpoint_path), device=device)

        state_module.marvis_model_loaded = True
        state_module.is_marvis_loading = False
        print("[Marvis Load] System successfully online and ready for generation requests!")

    except Exception as e:
        print(f"[Marvis Load Error] Failed to map model into memory: {e}")
        state_module.marvis_model_loaded = False
        state_module.is_marvis_loading = False

# ==========================================
# MARVIS DOWNLOAD & BOOT SYSTEM
# ==========================================
def start_marvis_setup(model_type="gpu"):
    import requests
    from ..config import base_dir
    import app.state as state_module
    
    marvis_dir = base_dir / "models" / "marvis"
    voices_dir = base_dir / "voices" / "marvis"
    marvis_dir.mkdir(parents=True, exist_ok=True)
    voices_dir.mkdir(parents=True, exist_ok=True)
    
    ckpt_path = marvis_dir / "marvis_tts.pt"
    
    if not ckpt_path.exists():
        print("[Marvis Setup] Downloading weights (This may take a while)...")
        # Direct URL to the pytorch weights
        url = "https://huggingface.co/marvis-labs/marvis-tts/resolve/main/marvis_tts.pt"
        
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(ckpt_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
                
    # Create the default dummy voice if it doesn't exist
    dummy_voice = voices_dir / "default"
    dummy_voice.mkdir(exist_ok=True)
    if not (dummy_voice / "ref.txt").exists():
        with open(dummy_voice / "ref.txt", "w") as f:
            f.write("This is a generated reference text.")
            
    # Auto-load after download
    load_marvis_into_memory(model_type)

def load_marvis_into_memory(model_type="gpu"):
    import torch
    import app.state as state_module
    from ..config import base_dir
    
    print("[Marvis Boot] Loading Marvis into memory...")
    ckpt_path = base_dir / "models" / "marvis" / "marvis_tts.pt"
    
    device = "cuda" if model_type == "gpu" and torch.cuda.is_available() else "cpu"
    
    # Using relative imports assuming you place the marvis_tts folder inside your app
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
    
    # Load Weights
    obj = torch.load(str(ckpt_path), weights_only=True, map_location="cpu")
    state_dict = obj["model_state"] if isinstance(obj, dict) and "model_state" in obj else obj
    raw_model.load_state_dict(state_dict, strict=True)
    raw_model.eval()
    
    state_module.marvis_generator = Generator(raw_model, text_tokenizer=state_module.marvis_tokenizer, device=device)
    state_module.marvis_model_loaded = True
    print(f"[Marvis Boot] Marvis Engine ready on {device.upper()}!")

    # ==========================================
# MARVIS DOWNLOAD & BOOT SYSTEM
# ==========================================
def start_marvis_setup(model_type="gpu"):
    import requests
    from ..config import base_dir
    import app.state as state_module
    
    print("[SETUP] Initiating Marvis Engine Download Sequence...")
    
    # 1. Define the exact folder structures needed
    marvis_dir = base_dir / "models" / "marvis"
    voices_dir = base_dir / "voices" / "marvis"
    
    # 2. Create the folders if they don't exist yet
    marvis_dir.mkdir(parents=True, exist_ok=True)
    voices_dir.mkdir(parents=True, exist_ok=True)
    
    ckpt_path = marvis_dir / "marvis_tts.pt"
    
    # 3. Download the model weights safely
    if not ckpt_path.exists():
        print(f"[SETUP] Downloading Marvis weights to {ckpt_path} (This will take a few minutes)...")
        try:
            # Direct URL to the pytorch weights
            url = "https://huggingface.co/marvis-labs/marvis-tts/resolve/main/marvis_tts.pt"
            
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            with open(ckpt_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk: 
                        f.write(chunk)
            print("[SETUP] Marvis weights downloaded successfully.")
        except Exception as e:
            print(f"[SETUP ERROR] Failed to download Marvis weights: {e}")
            return # Abort setup if download fails
            
    # 4. Create a Default "Template" Cloned Voice Folder
    # This prevents the UI from crashing if the user hasn't uploaded a clone yet.
    dummy_voice = voices_dir / "default"
    dummy_voice.mkdir(exist_ok=True)
    if not (dummy_voice / "ref.txt").exists():
        with open(dummy_voice / "ref.txt", "w", encoding="utf-8") as f:
            f.write("This is a default reference text for voice cloning.")
            
    # 5. Automatically load the model into memory now that we have the files
    print("[SETUP] Files verified. Triggering Marvis Memory Load...")
    load_marvis_into_memory(model_type)


def load_marvis_into_memory(model_type="gpu"):
    import torch
    import app.state as state_module
    from ..config import base_dir
    
    print("[ENGINE] Loading Marvis into memory...")
    ckpt_path = base_dir / "models" / "marvis" / "marvis_tts.pt"
    
    if not ckpt_path.exists():
        print("[ENGINE ERROR] Marvis weights not found. Setup aborted.")
        return
    
    device = "cuda" if model_type == "gpu" and torch.cuda.is_available() else "cpu"
    
    try:
        # Load the raw architecture from the marvis-tts repository
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
        
        # Load the downloaded weights securely into the architecture
        obj = torch.load(str(ckpt_path), weights_only=True, map_location="cpu")
        state_dict = obj["model_state"] if isinstance(obj, dict) and "model_state" in obj else obj
        raw_model.load_state_dict(state_dict, strict=True)
        raw_model.eval()
        
        # Bind it to the global state so tts.py can access it
        state_module.marvis_generator = Generator(raw_model, text_tokenizer=state_module.marvis_tokenizer, device=device)
        state_module.marvis_model_loaded = True
        
        print(f"[ENGINE] Marvis Engine fully online and ready on {device.upper()}!")
        
    except ImportError as e:
        print(f"[ENGINE ERROR] Missing Marvis Python Code: {e}")
        print("ACTION REQUIRED: Ensure the 'marvis_tts' repository is placed in your Python path.")
        state_module.marvis_model_loaded = False
    except Exception as e:
        print(f"[ENGINE ERROR] Failed to load Marvis: {e}")
        state_module.marvis_model_loaded = False