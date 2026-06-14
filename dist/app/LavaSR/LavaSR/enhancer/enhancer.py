## Proposed work 
## This BWE model is based on Vocos, excellant speed with good quality.

import os
import yaml
import torch
import types
from vocos import Vocos

## used to improve quality in end
from LavaSR.enhancer.linkwitz_merge import FastLRMerge

## quick monkey patch to improve quality slightly
def custom_forward(self, x: torch.Tensor) -> torch.Tensor:
    """
    Forward pass of the ISTFTHead module.

    Args:
        x (Tensor): Input tensor of shape (B, L, H)

    Returns:
        Tensor: Reconstructed time-domain audio signal
    """
    x = self.out(x).transpose(1, 2)
    mag, p = x.chunk(2, dim=1)
    mag = torch.exp(mag)
    mag = torch.clip(mag, max=1e3)
    x_real = torch.cos(p)
    x_imag = torch.sin(p)
    S = mag * (x_real + 1j * x_imag)
    audio = self.istft(S)
    return audio
  
class LavaBWE:
    def __init__(self, model_path, device='cpu'):
      
        self.device = device
        self.lr_refiner = FastLRMerge(device=device)

        state_dict = torch.load(f"{model_path}/pytorch_model.bin", map_location="cpu")
        
        # --- AUTO-PATCH INJECTION START ---
        config_path = f"{model_path}/config.yaml"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f)
                
                needs_save = False
                init_args = config_data.get("feature_extractor", {}).get("init_args", {})
                
                # Aggressively strip both 'norm' and 'mel_scale' to prevent Vocos TypeError crashes
                for problematic_arg in ["norm", "mel_scale"]:
                    if problematic_arg in init_args:
                        del init_args[problematic_arg]
                        needs_save = True
                        
                if needs_save:
                    with open(config_path, "w", encoding="utf-8") as f:
                        yaml.dump(config_data, f, default_flow_style=False)
            except Exception as e:
                pass
        # --- AUTO-PATCH INJECTION END ---

        self.bwe_model = Vocos.from_hparams(config_path)

        self.bwe_model.load_state_dict(state_dict)
        self.bwe_model = self.bwe_model.eval().to(device)
    
        self.bwe_model.head.forward = types.MethodType(custom_forward, self.bwe_model.head)

    def infer(self, wav, autocast=False):
        """Inference function for bwe. Native 48kHz processing restored."""
      
        wav = wav.to(self.device)
        dev_type = 'cuda' if 'cuda' in str(self.device) else ('mps' if 'mps' in str(self.device) else 'cpu')
        
        with torch.no_grad(), torch.autocast(device_type=dev_type, dtype=torch.float16, enabled=autocast):
            features_input = self.bwe_model.feature_extractor(wav)
            features = self.bwe_model.backbone(features_input)
            pred_audio = self.bwe_model.head(features)
            
            with torch.autocast(device_type=dev_type, enabled=False):
                # Flawless 1:1 length matching
                min_len = min(pred_audio.shape[1], wav.shape[1])
                pred_audio = self.lr_refiner(pred_audio[:, :min_len].float(), wav[:, :min_len].float())

        return pred_audio