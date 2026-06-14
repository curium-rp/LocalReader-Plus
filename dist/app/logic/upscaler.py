import os
import sys
import numpy as np
import threading
import traceback
import urllib.request

# Calculate all possible root directories to search
CURRENT_FILE = os.path.abspath(__file__)
LOGIC_DIR = os.path.dirname(CURRENT_FILE)
APP_DIR = os.path.dirname(LOGIC_DIR)
DIST_DIR = os.path.dirname(APP_DIR)
PROJECT_ROOT = os.path.dirname(DIST_DIR)

# Omni-Scanner will search all of these paths dynamically
SEARCH_DIRS = [
    os.path.join(DIST_DIR, "models"),
    os.path.join(PROJECT_ROOT, "models"),
    DIST_DIR,
    PROJECT_ROOT
]

code_parent_path = None
weights_root_path = None

print("[LavaSR] Initializing Omni-Scanner...")

# =====================================================================
# --- AUTO-DOWNLOADER & CONFIG REPAIR SYSTEM ---
# =====================================================================
DEFAULT_WEIGHTS_DIR = os.path.join(DIST_DIR, "models", "LavaSR")
V2_DIR = os.path.join(DEFAULT_WEIGHTS_DIR, "enhancer_v2")
BIN_PATH = os.path.join(V2_DIR, "pytorch_model.bin")
YAML_PATH = os.path.join(V2_DIR, "config.yaml")

# 1. Ensure the directories exist
os.makedirs(V2_DIR, exist_ok=True)

# 2. Auto-Generate missing config.yaml so Vocos never crashes
if not os.path.exists(YAML_PATH):
    print("[LavaSR] Auto-generating missing config.yaml...")
    yaml_content = """feature_extractor:
  class_path: vocos.feature_extractors.MelSpectrogramFeatures
  init_args:
    sample_rate: 44100
    n_fft: 2048
    hop_length: 512
    n_mels: 80
    padding: same

backbone:
  class_path: vocos.models.VocosBackbone
  init_args:
    input_channels: 80
    dim: 512
    intermediate_dim: 1536
    num_layers: 8

head:
  class_path: vocos.heads.ISTFTHead
  init_args:
    dim: 512
    n_fft: 2048
    hop_length: 512
    padding: same
"""
    with open(YAML_PATH, "w", encoding="utf-8") as f:
        f.write(yaml_content)

# 3. Auto-Download missing pytorch_model.bin (~50MB)
if not os.path.exists(BIN_PATH) or os.path.getsize(BIN_PATH) < 1000000:
    print("\n[LavaSR WARNING] Missing 50MB model file! Initiating Auto-Downloader...")
    
    # Change this URL if your specific file is hosted somewhere else
    MODEL_URL = "https://huggingface.co/Atsuraeru/LavaSR/resolve/main/enhancer_v2/pytorch_model.bin"
    
    try:
        req = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(BIN_PATH, 'wb') as out_file:
            total_size = int(response.info().get('Content-Length', 0))
            block_size = 1024 * 8
            count = 0
            while True:
                data = response.read(block_size)
                if not data:
                    break
                out_file.write(data)
                count += 1
                if total_size > 0:
                    percent = min(100, int(count * block_size * 100 / total_size))
                    sys.stdout.write(f"\r[LavaSR] Downloading Model... [{percent}%]")
                    sys.stdout.flush()
        print("\n[LavaSR] Download complete! Model is safely stored.\n")
    except Exception as e:
        print(f"\n[LavaSR ERROR] Auto-download failed: {e}")
        print("[LavaSR] Please manually download 'pytorch_model.bin' and place it inside 'models/LavaSR/enhancer_v2/'\n")
# =====================================================================


# --- OMNI-SCANNER CONTINUES ---
for search_dir in SEARCH_DIRS:
    if not os.path.exists(search_dir):
        continue
    
    for root, dirs, files in os.walk(search_dir):
        lower_files = [f.lower() for f in files]
        current_folder = os.path.basename(root).lower()
        
        # Look for the code inside the LavaSR folder
        if not code_parent_path and "model.py" in lower_files and "utils.py" in lower_files:
            if current_folder == "lavasr":
                code_parent_path = os.path.dirname(root)
                print(f"[LavaSR] Found Code Base at: {root}")
        
        # Look for the weights root (Strictly checking for the enhancer_v2 folder)
        if not weights_root_path and "enhancer_v2" in [d.lower() for d in dirs]:
            bin_check = os.path.join(root, "enhancer_v2", "pytorch_model.bin")
            yaml_check = os.path.join(root, "enhancer_v2", "config.yaml")
            
            if os.path.exists(bin_check) and os.path.exists(yaml_check):
                if os.path.getsize(bin_check) > 1000000:
                    weights_root_path = root
                    print(f"[LavaSR] Found fully validated Weights Base at: {root}")
                
    if code_parent_path and weights_root_path:
        break

if code_parent_path and code_parent_path not in sys.path:
    sys.path.insert(0, code_parent_path)

_upscaler_instance = None
_force_cpu_fallback = False
upscale_lock = threading.Lock()

def get_upscaler():
    global _upscaler_instance, _force_cpu_fallback
    if _upscaler_instance is None:
        with upscale_lock: # <-- ADDED: Blocks collision between User Click and Bootloader
            # Double-check inside the lock to prevent memory duplication
            if _upscaler_instance is None:
                try:
                    try:
                        import vocos
                    except ImportError:
                        raise ImportError("CRITICAL MISSING DEPENDENCY: 'vocos' is not installed.")

                    if not code_parent_path or not weights_root_path:
                        raise FileNotFoundError("Omni-Scanner failed to locate all required files.")
                        
                    from LavaSR.model import LavaEnhance2
                    from LavaSR.enhancer.linkwitz_merge import FastLRMerge
                    
                    # Helper function to initialize strictly on CPU
                    def _init_model(target_device):
                        import torch
                        import multiprocessing
                        
                        try:
                            max_cores = multiprocessing.cpu_count()
                            torch.set_num_threads(max_cores)
                        except Exception:
                            pass
                            
                        instance = LavaEnhance2(model_path=weights_root_path, device="cpu")
                        instance.bwe_model.lr_refiner = FastLRMerge(
                            cutoff=8000, 
                            device="cpu"
                        )
                        return instance

                    _upscaler_instance = _init_model("cpu")
                    print(f"[LavaSR] Loaded successfully on CPU")
                    
                except Exception as e:
                    print(f"\n[LavaSR CRITICAL] INITIALIZATION COMPLETELY FAILED")
                    print(f"Error details: {e}")
                    traceback.print_exc()
                    return None
            
    return _upscaler_instance

def apply_upscale(audio_array: np.ndarray, current_sr: int) -> tuple[np.ndarray, int]:
    global _upscaler_instance, _force_cpu_fallback
    upscaler = get_upscaler()
    
    if upscaler is None:
        return audio_array, current_sr
    
    import torch
    import torchaudio
    import numpy as np
    
    FINAL_SR = 48000
    exact_target_length = int(len(audio_array) * (FINAL_SR / current_sr))
    
    with upscale_lock:
        try:
            wav_tensor = torch.from_numpy(audio_array).float().to('cpu')
            
            if wav_tensor.dim() == 1:
                wav_tensor = wav_tensor.unsqueeze(0)
            
            if current_sr != 16000:
                wav_tensor = torchaudio.functional.resample(wav_tensor, current_sr, 16000)
            
            chunk_size = 16000 * 60
            total_length = wav_tensor.shape[-1]
            enhanced_chunks = []
            
            for start in range(0, total_length, chunk_size):
                end = min(start + chunk_size, total_length)
                chunk = wav_tensor[..., start:end]
                
                enhanced_chunk = upscaler.enhance(chunk, enhance=True, denoise=False)
                
                if enhanced_chunk.dim() == 1:
                    enhanced_chunk = enhanced_chunk.unsqueeze(0)
                
                enhanced_chunks.append(enhanced_chunk.cpu())
            
            if len(enhanced_chunks) > 1:
                output_tensor = torch.cat(enhanced_chunks, dim=-1)
            else:
                output_tensor = enhanced_chunks[0]
                
            output_array = output_tensor.squeeze().numpy()
            
            if len(output_array) > exact_target_length:
                output_array = output_array[:exact_target_length]
            elif len(output_array) < exact_target_length:
                pad_amount = exact_target_length - len(output_array)
                output_array = np.pad(output_array, (0, pad_amount), mode='constant')
                
            return output_array, FINAL_SR
            
        except Exception as e:
            print(f"\n[LavaSR CRITICAL] CPU Math crashed during execution!")
            print(f"[Error Type] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
                
            return audio_array, current_sr
            
        finally:
            if 'wav_tensor' in locals(): del wav_tensor
            if 'chunk' in locals(): del chunk
            if 'enhanced_chunk' in locals(): del enhanced_chunk
            if 'output_tensor' in locals(): del output_tensor

# --- AUTO BOOT-LOADER START ---
def _background_bootloader():
    try:
        print("[LavaSR] Background Boot-Loader initiated. Pre-loading CPU model into RAM...")
        get_upscaler()
        print("[LavaSR] Boot-Loader complete. Upscaler is standing by for instant execution.")
    except Exception:
        pass

threading.Thread(target=_background_bootloader, daemon=True).start()
# --- AUTO BOOT-LOADER END ---