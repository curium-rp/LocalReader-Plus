import os
import sys
import numpy as np
import threading

# Locate our custom download folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAVASR_DIR = os.path.join(BASE_DIR, "models", "LavaSR")

# Inject the folder into Python's path so 'from LavaSR.model import ...' works
if LAVASR_DIR not in sys.path:
    sys.path.insert(0, LAVASR_DIR)

_upscaler_instance = None
upscale_lock = threading.Lock()

def get_upscaler():
    global _upscaler_instance
    if _upscaler_instance is None:
        try:
            from LavaSR.model import LavaEnhance2
            from LavaSR.enhancer.linkwitz_merge import FastLRMerge
            import torch
            
            # Hardware acceleration check
            if torch.cuda.is_available():
                device = 'cuda'
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = 'mps'
            else:
                device = 'cpu'
                
            _upscaler_instance = LavaEnhance2(model_path=LAVASR_DIR, device=device)
            
            # SURGICAL FIX 1: V2 Audio Muffling
            # V2 defaults its low-pass merger to 12000Hz. Because we downsample Kokoro to 16000Hz 
            # (which has a max frequency of 8000Hz), everything between 8kHz and 12kHz was deleted!
            # We explicitly override the cutoff to 8000Hz so LavaSR perfectly restores the lost band.
            _upscaler_instance.bwe_model.lr_refiner = FastLRMerge(
                target_sr=48000, 
                cutoff=8000, 
                device=device
            )
            
            print(f"[LavaSR] Loaded successfully on {device} (Cutoff optimized for Kokoro)")
            
        except ImportError:
            print("[LavaSR] Code not found. Please download via the UI.")
            return None
            
    return _upscaler_instance

def apply_upscale(audio_array: np.ndarray, current_sr: int) -> tuple[np.ndarray, int]:
    upscaler = get_upscaler()
    if upscaler is None:
        return audio_array, current_sr
    
    import torch
    import torchaudio
    
    target_sr = 48000
    # Calculate the mathematically exact target length to prevent WebAudio UI pauses
    exact_target_length = int(len(audio_array) * (target_sr / current_sr))
    
    with upscale_lock:
        wav_tensor = torch.from_numpy(audio_array).float().to(upscaler.device)
        
        # LavaSR strictly requires 16kHz input.
        if current_sr != 16000:
            wav_tensor = torchaudio.functional.resample(wav_tensor, current_sr, 16000)
            
        # SURGICAL FIX 2: We MUST use batch=True. 
        # If batch=False, the 1D tensor crashes LavaSR internally with `wav.shape[1]`.
        enhanced_tensor = upscaler.enhance(wav_tensor, enhance=True, denoise=False, batch=True)
        
        output_array = enhanced_tensor.cpu().numpy().squeeze()
        
        # SURGICAL FIX 3: Strip the batching silence!
        # LavaSR's `batch=True` injects up to 1 second of pure silence padding at the end.
        # This padding caused the UI WebAudio to "think longer" and pause awkwardly between sentences.
        if len(output_array) > exact_target_length:
            output_array = output_array[:exact_target_length]
            
        del wav_tensor
        del enhanced_tensor
        
        # Clean VRAM so the GPU doesn't crash on long books
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        return output_array, target_sr