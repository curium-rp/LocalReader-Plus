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
# FISH SPEECH DOWNLOAD & BOOT SYSTEM
# ==========================================
import torch
from huggingface_hub import snapshot_download

# ==========================================
# FISH-TTS SETUP & MEMORY MANAGEMENT
# ==========================================

def start_fish_setup():
    """
    Downloads the Fish-Speech 1.5 model from HuggingFace to the local models directory.
    This runs in a background thread triggered by the UI Setup button.
    """
    # SURGICAL FIX: Use Absolute Imports to prevent the Relative Import Crash!
    from app.config import base_dir
    from huggingface_hub import snapshot_download
    
    model_dir = base_dir / "models" / "fish" / "fish-speech-1.5"
    model_dir.mkdir(parents=True, exist_ok=True)
    
    print("[FISH-SETUP] Starting download for Fish-Speech 1.5 weights...")
    try:
        # Downloads the core LLM and the Firefly VQ decoder together
        snapshot_download(
            repo_id="fishaudio/fish-speech-1.5",
            local_dir=str(model_dir),
            ignore_patterns=["*.pt"] 
        )
        print("[FISH-SETUP] Download complete!")
        
        # Ensure the default voice folder exists so it doesn't crash on first run
        default_voice_dir = base_dir / "voices" / "fish" / "default"
        default_voice_dir.mkdir(parents=True, exist_ok=True)
        
    except Exception as e:
        print(f"[FISH-SETUP ERROR] Failed to download model: {str(e)}")
        raise e

def load_fish_into_memory():
    """
    Instantiates the Fish-TTS engine into VRAM using the self-healing GitHub v1.5.0 bypass.
    """
    from app.config import base_dir
    import app.state as state_module
    import torch
    from pathlib import Path
    import sys
    import os
    import subprocess
    import importlib

    # ==========================================
    # SURGICAL FIX 1: Auto-Install Tokenizers (Prevents Silence)
    # Automatically installs tiktoken/sentencepiece and clears the import cache
    # so Python instantly recognizes them without needing a server restart.
    # ==========================================
try:
        import tiktoken
        import sentencepiece
        import funasr
    except ImportError:
        print("[FISH-TTS] Missing dependencies detected! Auto-installing tiktoken, sentencepiece, and funasr...")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", 
                "tiktoken", "sentencepiece", "transformers", "funasr", "modelscope"
            ])
            importlib.invalidate_caches()  # Force Python to reload available packages
            print("[FISH-TTS] Dependencies installed successfully!")
        except Exception as e:
            print(f"[FISH-TTS ERROR] Failed to auto-install dependencies: {e}")

    import urllib.request
    import zipfile
    import io
    import shutil

    model_dir = base_dir / "models" / "fish" / "fish-speech-1.5"
    repo_dir = base_dir / "models" / "fish" / "fish-speech-repo"

    if not model_dir.exists():
        raise FileNotFoundError(f"Fish-TTS model directory not found at {model_dir}. Please run setup.")

    # ==========================================
    # SURGICAL FIX 2: Pin GitHub Version to v1.5.0 (Cures Missing Config)
    # The 'main' branch updated to 'dual_ar'. We specifically download the v1.5.0
    # release so the 'firefly_gan_vq' configs perfectly match your weights.
    # ==========================================
    if not repo_dir.exists() or not (repo_dir / "configs").exists():
        print("[FISH-TTS] Downloading pristine GitHub v1.5.0 repository to fix config compatibility...")
        url = "https://github.com/fishaudio/fish-speech/archive/refs/tags/v1.5.0.zip"
        try:
            with urllib.request.urlopen(url) as response:
                with zipfile.ZipFile(io.BytesIO(response.read())) as zip_ref:
                    zip_ref.extractall(base_dir / "models" / "fish")
            
            # The extracted folder from the v1.5.0 tag zip is named 'fish-speech-1.5.0'
            extracted_dir = base_dir / "models" / "fish" / "fish-speech-1.5.0"
            
            # Safe fallback just in case GitHub changes the zip structure
            if not extracted_dir.exists():
                dirs = [d for d in (base_dir / "models" / "fish").iterdir() if d.is_dir() and "fish-speech" in d.name and d.name not in ["fish-speech-1.5", "fish-speech-repo"]]
                if dirs:
                    extracted_dir = dirs[0]

            if extracted_dir.exists():
                if repo_dir.exists():
                    shutil.rmtree(repo_dir)
                extracted_dir.rename(repo_dir)
        except Exception as e:
            raise RuntimeError(f"Failed to download v1.5.0 repository: {e}")

    # ==========================================
    # SURGICAL FIX 3: Memory Purge & Path Injection
    # Force Python to use the pristine GitHub repo instead of pip
    # ==========================================
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    keys_to_remove = [k for k in sys.modules.keys() if k.startswith("fish_speech") or k.startswith("tools")]
    for k in keys_to_remove:
        del sys.modules[k]

    # Bypass pyrootutils crash for local installations
    try:
        import pyrootutils
        pyrootutils.setup_root = lambda *args, **kwargs: repo_dir
    except ImportError:
        pass

    print(f"[FISH-TTS] Loading model from {model_dir} into GPU Memory...")

    try:
        from tools.server.model_manager import ModelManager
        
        # 1. Find the exact Firefly decoder dynamically
        decoder_path = model_dir / "firefly-gan-vq-fsq-8x1024-21hz-generator.pth"
        if not decoder_path.exists():
            pth_files = list(model_dir.glob("*.pth"))
            if pth_files:
                decoder_path = pth_files[0]
            else:
                raise FileNotFoundError(f"Could not find decoder .pth file in {model_dir}")

        device = "cuda" if torch.cuda.is_available() else "cpu"
        is_half = not (torch.cuda.is_available() and torch.cuda.is_bf16_supported())

        print(f"[FISH-TTS] Booting ModelManager with LLAMA Queue & {decoder_path.name}...")

        # ==========================================
        # SURGICAL FIX 4: The Hydra Config Anchor
        # Anchor the execution directory so Hydra perfectly loads the config file
        # ==========================================
        original_cwd = os.getcwd()
        os.chdir(str(repo_dir))

        try:
            manager = ModelManager(
                mode="tts",
                device=device,
                half=is_half,
                compile=False,
                llama_checkpoint_path=str(model_dir),
                decoder_checkpoint_path=str(decoder_path),
                decoder_config_name="firefly_gan_vq"
            )
        finally:
            # ALWAYS restore the working directory so LocalReader doesn't break
            os.chdir(original_cwd)

        # Extract the fully initialized and warmed-up engine
        state_module.fish_engine = manager.tts_inference_engine
        state_module.fish_model_loaded = True
        
        print("[FISH-TTS] Engine successfully loaded and warmed up in VRAM!")

    except Exception as e:
        state_module.fish_engine = None
        state_module.fish_model_loaded = False
        import traceback
        traceback.print_exc()
        raise RuntimeError(f"Failed to load Fish-TTS into memory: {str(e)}")