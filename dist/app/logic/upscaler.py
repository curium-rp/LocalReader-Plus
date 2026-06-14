import os
import sys
import numpy as np
import threading
import traceback

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

print("[LavaSR] Initializing Omni-Scanner for strict 4-file validation...")

for search_dir in SEARCH_DIRS:
    if not os.path.exists(search_dir):
        continue
    
    for root, dirs, files in os.walk(search_dir):
        lower_files = [f.lower() for f in files]
        current_folder = os.path.basename(root).lower()
        
        # 1. Look for the code inside the LavaSR folder
        if not code_parent_path and "model.py" in lower_files and "utils.py" in lower_files:
            if current_folder == "lavasr":
                code_parent_path = os.path.dirname(root)
                print(f"[LavaSR] Found Code Base at: {root}")
        
        # 2. Look for the weights root (Must contain both enhancer_v2 and denoiser)
        if not weights_root_path and "enhancer_v2" in [d.lower() for d in dirs] and "denoiser" in [d.lower() for d in dirs]:
            bin_path = os.path.join(root, "enhancer_v2", "pytorch_model.bin")
            yaml_path = os.path.join(root, "enhancer_v2", "config.yaml")
            denoiser_path = os.path.join(root, "denoiser", "denoiser.bin")
            
            # Strictly enforce ALL required files exist
            if os.path.exists(bin_path) and os.path.exists(yaml_path) and os.path.exists(denoiser_path):
                if os.path.getsize(bin_path) > 1000000:
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
        try:
            # Vocoder Dependency Check
            try:
                import vocos
            except ImportError:
                raise ImportError("CRITICAL MISSING DEPENDENCY: 'vocos' is not installed. Open your terminal and run: pip install vocos")

            if not code_parent_path or not weights_root_path:
                raise FileNotFoundError("Omni-Scanner failed to locate all 4 required files (model.py, pytorch_model.bin, config.yaml, denoiser.bin).")
                
            from LavaSR.model import LavaEnhance2
            from LavaSR.enhancer.linkwitz_merge import FastLRMerge
            import torch
            
            # Hardware Detection
            if torch.cuda.is_available() and not _force_cpu_fallback:
                primary_device = 'cuda'
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available() and not _force_cpu_fallback:
                primary_device = 'mps'
            else:
                primary_device = 'cpu'
                
            # Helper function to initialize so we can attempt it multiple times
            def _init_model(target_device):
                instance = LavaEnhance2(model_path=weights_root_path, device=target_device)
                instance.bwe_model.lr_refiner = FastLRMerge(
                    target_sr=48000, 
                    cutoff=8000, 
                    device=target_device
                )
                return instance

            # ATTEMPT 1: Load on Primary Hardware Accelerator
            try:
                _upscaler_instance = _init_model(primary_device)
                print(f"[LavaSR] Loaded successfully on {primary_device.upper()}")
            except Exception as load_e:
                # ATTEMPT 2: Fallback to CPU if GPU initialization fails (e.g., Driver issue)
                if primary_device != 'cpu':
                    print(f"\n[LavaSR WARNING] Failed to initialize on {primary_device.upper()}: {load_e}")
                    print("[LavaSR] Attempting CPU Fallback for initialization...")
                    
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        
                    _force_cpu_fallback = True
                    _upscaler_instance = _init_model('cpu')
                    print("[LavaSR] Loaded successfully on CPU (Fallback Active)\n")
                else:
                    raise load_e
            
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
    
    target_sr = 48000
    exact_target_length = int(len(audio_array) * (target_sr / current_sr))
    
    with upscale_lock:
        try:
            wav_tensor = torch.from_numpy(audio_array).float().to(upscaler.device)
            
            # 1D Shape Crash Fix
            if wav_tensor.dim() == 1:
                wav_tensor = wav_tensor.unsqueeze(0)
            
            if current_sr != 16000:
                wav_tensor = torchaudio.functional.resample(wav_tensor, current_sr, 16000)
            
            # VRAM Explosion Chunking (5-second max safe limits)
            chunk_size = 16000 * 5
            total_length = wav_tensor.shape[-1]
            enhanced_chunks = []
            
            for start in range(0, total_length, chunk_size):
                end = min(start + chunk_size, total_length)
                chunk = wav_tensor[..., start:end]
                
                enhanced_chunk = upscaler.enhance(chunk, enhance=True, denoise=False)
                enhanced_chunks.append(enhanced_chunk.cpu().squeeze())
            
            # Reassemble the safe chunks
            if len(enhanced_chunks) > 1:
                output_tensor = torch.cat(enhanced_chunks, dim=-1)
            else:
                output_tensor = enhanced_chunks[0]
                
            output_array = output_tensor.numpy()
            
            if len(output_array) > exact_target_length:
                output_array = output_array[:exact_target_length]
                
            return output_array, target_sr
            
        except Exception as e:
            print(f"\n[LavaSR CRITICAL] Tensor Math crashed during execution!")
            print(f"[Error Type] {type(e).__name__}: {e}")
            traceback.print_exc()
            
            # RUNTIME CPU FALLBACK: If the GPU crashes during math (e.g., Out of Memory), 
            # lock to CPU mode so the NEXT sentence succeeds.
            if upscaler.device != 'cpu':
                print("[LavaSR] Hardware execution failed. Forcing CPU mode for all future requests.")
                _upscaler_instance = None
                _force_cpu_fallback = True
                
            print("[LavaSR CRITICAL] Safety fallback triggered. Outputting standard Kokoro audio...\n")
            return audio_array, current_sr
            
        finally:
            # Total VRAM Cleanup
            if 'wav_tensor' in locals(): del wav_tensor
            if 'chunk' in locals(): del chunk
            if 'enhanced_chunk' in locals(): del enhanced_chunk
            if 'output_tensor' in locals(): del output_tensor
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()