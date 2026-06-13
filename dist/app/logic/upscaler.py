import os
import sys
import numpy as np

# Locate our custom download folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAVASR_DIR = os.path.join(BASE_DIR, "models", "LavaSR")

# Inject the folder into Python's path so 'from LavaSR.model import ...' works
if LAVASR_DIR not in sys.path:
    sys.path.insert(0, LAVASR_DIR)

_upscaler_instance = None

def get_upscaler():
    global _upscaler_instance
    if _upscaler_instance is None:
        try:
            from LavaSR.model import LavaEnhance2
            import torch
            
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            # Pass our custom folder path so it skips HuggingFace and loads locally
            _upscaler_instance = LavaEnhance2(model_path=LAVASR_DIR, device=device)
            print(f"[LavaSR] Loaded successfully on {device}")
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
    
    # 1. Convert numpy array to torch tensor
    wav_tensor = torch.from_numpy(audio_array).float().to(upscaler.device)
    
    # 2. LavaSR strictly requires 16kHz input. Resample if Kokoro gave us 24kHz.
    if current_sr != 16000:
        wav_tensor = torchaudio.functional.resample(wav_tensor, current_sr, 16000)
    
    # 3. Enhance! (We turn denoise=False because Kokoro output is already noise-free)
    enhanced_tensor = upscaler.enhance(wav_tensor, enhance=True, denoise=False)
    
    # 4. LavaSR always outputs 48kHz
    return enhanced_tensor.cpu().numpy(), 48000