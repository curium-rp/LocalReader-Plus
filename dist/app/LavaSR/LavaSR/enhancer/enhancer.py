## Proposed work 
## This BWE model is based on Vocos, excellant speed with good quality.


import torch
import types
import yaml
from vocos import Vocos
from torch.cuda.amp import autocast as autocast_func

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
        
        # ==========================================
        # SURGICAL FIX: Vocos Version Conflict Patcher
        # ==========================================
        # Newer versions of Vocos removed 'f_min' and 'f_max'. We intercept the 
        # config.yaml, delete the unsupported arguments, and save it back instantly.
        config_path = f"{model_path}/config.yaml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
                
            needs_patch = False
            if "feature_extractor" in config_data and "init_args" in config_data["feature_extractor"]:
                init_args = config_data["feature_extractor"]["init_args"]
                if "f_min" in init_args:
                    del init_args["f_min"]
                    needs_patch = True
                if "f_max" in init_args:
                    del init_args["f_max"]
                    needs_patch = True
                    
            if needs_patch:
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(config_data, f)
                print("[LavaSR] Successfully auto-patched config.yaml for Vocos compatibility.")
        except Exception as e:
            print(f"[LavaSR WARNING] Could not auto-patch config.yaml: {e}")

        # Boot Vocos using the safely cleaned config
        self.bwe_model = Vocos.from_hparams(config_path)

        self.bwe_model.load_state_dict(state_dict)
        self.bwe_model = self.bwe_model.eval().to(device)
    
        self.bwe_model.head.forward = types.MethodType(custom_forward, self.bwe_model.head)

        

    def infer(self, wav, autocast=False):
        """Inference function for bwe"""
      
        wav = wav.to(self.device)
        with torch.no_grad(), torch.autocast(self.device, dtype=torch.float16, enabled=autocast):
            features_input = self.bwe_model.feature_extractor(wav)
            features = self.bwe_model.backbone(features_input)
            pred_audio = self.bwe_model.head(features)
            with autocast_func(enabled=False):
                pred_audio = self.lr_refiner(pred_audio[:, :wav.shape[1]].float(), wav[:, :pred_audio.shape[1]].float())

        return pred_audio