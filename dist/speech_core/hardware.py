"""ONNX Runtime execution providers: linked once by the launcher, read by the engine."""

import glob
import os
import platform
import sys
from pathlib import Path

_ENV = "ORT_AUTO_PROVIDERS"


def has_onnxruntime_gpu() -> bool:
    capi = Path(sys.prefix) / "Lib" / "site-packages" / "onnxruntime" / "capi"
    return (capi / "onnxruntime_providers_cuda.dll").exists() or (
        capi / "libonnxruntime_providers_cuda.so"
    ).exists()


def configured_providers():
    """The providers the launcher linked, in priority order."""
    return [p for p in os.environ.get(_ENV, "").split(",") if p]


def _add_dll_directory(path):
    os.add_dll_directory(path)
    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def _link_windows_cuda():
    """Put CUDA/cuDNN on the DLL path: the pip 'nvidia' packages first, then a system install."""
    try:
        for entry in sys.path:
            nvidia_dir = os.path.join(entry, "nvidia")
            if os.path.isdir(nvidia_dir):
                for bin_path in glob.glob(os.path.join(nvidia_dir, "*", "bin")):
                    if os.path.isdir(bin_path):
                        _add_dll_directory(bin_path)
                print("[NVIDIA] Successfully linked CUDA/cuDNN from Python 'nvidia' package.")
                return True
    except Exception as e:
        print(f"[NVIDIA] Python environment DLLs not found ({e}). Falling back to System Scan...")

    found = []
    cuda = sorted(glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin"), reverse=True)
    if cuda and os.path.exists(cuda[0]):
        found.append(cuda[0])
    cudnn = (glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin\*\x64")
             or glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin"))
    cudnn.sort(reverse=True)
    if cudnn and os.path.exists(cudnn[0]):
        found.append(cudnn[0])
    for path in found:
        try:
            _add_dll_directory(path)
        except Exception:
            pass
    if found:
        print(f"NVIDIA DLLs Linked -> {' | '.join(found)}")
    return bool(found)


def link_execution_providers():
    """Detect this machine's accelerators before ONNX Runtime loads, and publish
    them for the engine (and any child process) through the environment."""
    if not has_onnxruntime_gpu():
        print("[ORT] onnxruntime-gpu not installed, skip GPU execution providers")
        providers = ["CPUExecutionProvider"]
    else:
        system = platform.system()
        providers = []
        if system == "Windows":
            print("[Check OS] Windows detected.")
            print("[ORT] onnxruntime-gpu found, linking CUDA")
            if _link_windows_cuda():
                providers.append("CUDAExecutionProvider")
            providers += ["DmlExecutionProvider", "OpenVINOExecutionProvider"]
        elif system == "Darwin":
            print("[Check OS] macOS detected. Link Core ML")
            providers.append("CoreMLExecutionProvider")
        elif system == "Linux":
            print("[Check OS] Linux detected. Configuring GPU support...")
            providers += ["CUDAExecutionProvider", "ROCMExecutionProvider", "OpenVINOExecutionProvider"]

    os.environ[_ENV] = ",".join(providers)
    names = [p.replace("ExecutionProvider", "") for p in providers]
    print(f" OS Hardware Acceleration Priority: {' -> '.join(names)}")
    return providers
