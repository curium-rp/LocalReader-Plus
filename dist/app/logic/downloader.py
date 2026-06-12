import os
import shutil
import requests
from huggingface_hub import hf_hub_download, snapshot_download
from typing import Literal
from pathlib import Path

# ==========================================
# KOKORO DOWNLOAD SYSTEM
# ==========================================
def download_kokoro_model(model_type: Literal["gpu", "cpu"] = "gpu") -> None:
    """
    Download the specified TTS model (Standard or Quantized).
    """
    target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    os.makedirs(target_dir, exist_ok=True)

    print(f"--- LocalReader Downloader (Dual-Engine) ---")
    print(f"Target: {target_dir}")
    print(f"Mode: {model_type.upper()}\n")

    if model_type == "cpu":
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

    if not os.path.exists(model_dest):
        print(f"Downloading {model_label} ({model_size})...")
        try:
            if model_type == "cpu":
                print(f"  Starting download from: {model_url}")
                r = requests.get(model_url, stream=True, timeout=600)  
                r.raise_for_status()
                
                total_size = int(r.headers.get('content-length', 0))
                total_size_mb = total_size / (1024 * 1024) if total_size > 0 else 0
                downloaded = 0
                
                with open(model_dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                downloaded_mb = downloaded / (1024 * 1024)
                                print(f"  Progress: {progress:.1f}% ({downloaded_mb:.1f}/{total_size_mb:.1f} MB)", end='\r')
                
                print(f"\n  [OK] {model_label} saved as kokoro.int8.onnx")
            else:
                path = hf_hub_download(repo_id=model_repo, filename=model_remote_path, local_dir=target_dir)
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
            old_json = os.path.join(target_dir, "voices.json")
            if os.path.exists(old_json):
                os.remove(old_json)
        except Exception as e:
            print(f"Voice Pack download failed: {e}")
            raise
    else:
        print("Voice Pack already exists (shared between both engines).")

    onnx_folder = os.path.join(target_dir, "onnx")
    if os.path.exists(onnx_folder):
        shutil.rmtree(onnx_folder)
    
    print(f"\nDownload complete! Active mode: {model_type.upper()}")

# ==========================================
# F5-TTS DOWNLOAD & BOOT SYSTEM
# ==========================================
def start_f5_setup():
    """
    Downloads F5-TTS v1 Base and its required Vocoder explicitly into models/f5
    """
    import app.config as config_module
    
    base_dir = config_module.base_dir
    f5_dir = base_dir / "models" / "f5"
    f5_dir.mkdir(parents=True, exist_ok=True)
    
    print("\n[SETUP] Initiating F5-TTS Engine Download Sequence...")
    print(f"[SETUP] Target Directory: {f5_dir}")
    
    try:
        # 1. Download F5-TTS v1 Base Weights
        print("[SETUP] Downloading F5-TTS v1 Base model (This is large and may take a few minutes)...")
        snapshot_download(
            repo_id="SWivid/F5-TTS",
            allow_patterns=["F5TTS_v1_Base/*"],
            local_dir=str(f5_dir / "SWivid" / "F5-TTS")
        )
        
        # 2. Download Vocos Vocoder
        print("[SETUP] Downloading Vocos Vocoder (Required for Audio Generation)...")
        snapshot_download(
            repo_id="charactr/vocos-mel-24khz",
            local_dir=str(f5_dir / "charactr" / "vocos-mel-24khz")
        )
        
        # 3. Create Default Voice Setup
        voices_dir = base_dir / "voices" / "f5" / "default"
        voices_dir.mkdir(parents=True, exist_ok=True)
        if not (voices_dir / "ref.txt").exists():
            with open(voices_dir / "ref.txt", "w", encoding="utf-8") as f:
                f.write("This is a default reference text for voice cloning.")
                
        print("[SETUP] F5-TTS weights downloaded successfully.")
    except Exception as e:
        print(f"[SETUP ERROR] Failed to download F5-TTS weights: {e}")
        raise e

def load_f5_into_memory():
    """
    Loads F5-TTS into VRAM utilizing the local weights downloaded during setup.
    """
    import app.state as state_module
    import app.config as config_module
    import torch
    
    try:
        from f5_tts.api import F5TTS
    except ImportError:
        print("[ENGINE ERROR] 'f5-tts' package is not installed! Please run: pip install f5-tts")
        state_module.f5_model_loaded = False
        return

    base_dir = config_module.base_dir
    f5_dir = base_dir / "models" / "f5"
    
    print("[ENGINE] Loading F5-TTS into memory...")
    
    # Expected paths if downloaded via our start_f5_setup
    ckpt_file = f5_dir / "SWivid" / "F5-TTS" / "F5TTS_v1_Base" / "model_1250000.safetensors"
    vocab_file = f5_dir / "SWivid" / "F5-TTS" / "F5TTS_v1_Base" / "vocab.txt"
    vocoder_path = f5_dir / "charactr" / "vocos-mel-24khz"
    
    # Safely assign paths (if empty, F5TTS API will attempt to auto-download via cache)
    final_ckpt = str(ckpt_file) if ckpt_file.exists() else ""
    final_vocab = str(vocab_file) if vocab_file.exists() else ""
    final_vocoder = str(vocoder_path) if vocoder_path.exists() else None
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    try:
        # F5TTS class auto-loads weights, vocoder, and places them on the device
        state_module.f5_model = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=final_ckpt,
            vocab_file=final_vocab,
            vocoder_local_path=final_vocoder,
            device=device,
            hf_cache_dir=str(f5_dir)
        )
        state_module.f5_model_loaded = True
        print(f"[ENGINE] F5-TTS successfully loaded on {device.upper()}!")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[ENGINE ERROR] Failed to load F5-TTS: {e}")
        state_module.f5_model_loaded = False


# ==========================================
# SYSTEM HELPERS
# ==========================================
def check_model_exists(model_type: Literal["gpu", "cpu"]) -> bool:
    target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    if model_type == "cpu":
        return os.path.exists(os.path.join(target_dir, "kokoro.int8.onnx"))
    return os.path.exists(os.path.join(target_dir, "kokoro.onnx"))

def get_available_models() -> dict:
    target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    f5_dir = os.path.join(target_dir, "f5")
    return {
        "gpu": check_model_exists("gpu"),
        "cpu": check_model_exists("cpu"),
        "voices": os.path.exists(os.path.join(target_dir, "voices.bin")),
        "f5": os.path.exists(f5_dir)
    }

if __name__ == "__main__":
    import sys
    model_type = sys.argv[1] if len(sys.argv) > 1 else "gpu"
    if model_type not in ["gpu", "cpu"]:
        print("Usage: python downloader.py [gpu|cpu]")
        sys.exit(1)
    download_kokoro_model(model_type)