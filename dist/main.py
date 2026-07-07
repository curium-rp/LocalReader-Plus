import os
import sys
import glob
import time
import socket
import threading
import uvicorn
import webview
import platform
from pathlib import Path

# --- 1. ARCHITECTURAL SETUP: ABSOLUTE PATH ANCHORING ---
base_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(base_dir))

# --- 2. UNIVERSAL HARDWARE DETECTION & ORT PROVIDER LINKING ---
# This block dynamically builds the optimal ONNX Runtime execution provider list
# based on the host OS, utilizing auto-fallback mechanics to guarantee stability.

optimized_providers = []

if platform.system() == "Windows":
    print("[Check OS] Windows detected. Scanning NVIDIA  CUDA | CUDNN ")
    found_paths = []
    ####install paths location need to change if you do change a files lcoation of installion of CUDAv12
    # --- MANUAL CUSTOM PATHS --- #
    # Uncomment and add your paths if installed in a custom location. #
    # custom_cuda = r"D:\Your\Custom\Folder\CUDA\v12.6\bin"
    # custom_cudnn = r"C:\Your\Custom\Folder\NVIDIA\CUDNN\v9.XX\bin\x64"
    
    # if 'custom_cuda' in locals() and os.path.exists(custom_cuda):
    #     found_paths.append(custom_cuda)
    # if 'custom_cudnn' in locals() and os.path.exists(custom_cudnn):
    #     found_paths.append(custom_cudnn)

    cuda_paths = glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin")
    if cuda_paths:
        cuda_paths.sort(reverse=True)
        best_cuda = cuda_paths[0]
        if os.path.exists(best_cuda):
            found_paths.append(best_cuda)
            
    #### Paths for CUDNN v9 (e.g., bin\12.9\x64)###
    cudnn_paths = glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin\*\x64")
    if not cudnn_paths:
        # Fallback for older path installation
        cudnn_paths = glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin")
        
    if cudnn_paths:
        cudnn_paths.sort(reverse=True) # Prioritizes newest version (e.g. 12.9 over 11.8)
        best_cudnn = cudnn_paths[0]
        if os.path.exists(best_cudnn):
            found_paths.append(best_cudnn)
            
    # Apply paths and print exactly once
    if found_paths:
        for p in found_paths:
            try:
                os.add_dll_directory(p)
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass
        print(f"NVIDIA DLLs Linked -> {' | '.join(found_paths)}")
        
        # 1. NVIDIA Priority (Windows)
        optimized_providers.append("CUDAExecutionProvider")
    
    # 2. AMD / Intel GPU Priority (Windows)
    # DirectML acts as the universal bridge for non-NVIDIA cards on Windows
    optimized_providers.append("DmlExecutionProvider")
    
    # 3. Dedicated Intel Fallback
    optimized_providers.append("OpenVINOExecutionProvider")

elif platform.system() == "Darwin":
    print("[Check OS] macOS detected. Link Core ML")
    # 1. Apple Silicon Priority (M1/M2/M3)
    optimized_providers.append("CoreMLExecutionProvider")

elif platform.system() == "Linux":
    print("[CHeck OS] Linux detected. Configuring GPU support...")
    # --- MANUAL CUSTOM PATHS (LINUX) --- #
    # Linux uses LD_LIBRARY_PATH instead of DLL directories.
    # Uncomment and add your paths if installed in a custom location.
    # custom_cuda_linux = "/usr/local/cuda-12.6/lib64"
    # custom_cudnn_linux = "/usr/local/cudnn/lib"
    # if 'custom_cuda_linux' in locals() and os.path.exists(custom_cuda_linux):
    #     os.environ["LD_LIBRARY_PATH"] = custom_cuda_linux + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    # if 'custom_cudnn_linux' in locals() and os.path.exists(custom_cudnn_linux):
    #     os.environ["LD_LIBRARY_PATH"] = custom_cudnn_linux + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    
    # Linux native environment variables handle the routing, no DLL injection required except you change path.
    # 1. NVIDIA Priority (Linux)
    optimized_providers.append("CUDAExecutionProvider")
    
    # 2. AMD Priority (Linux ROCm)
    optimized_providers.append("ROCMExecutionProvider")
    
    # 3. Intel Priority (Linux OpenVINO)
    optimized_providers.append("OpenVINOExecutionProvider")

# Pass the OS hardware wishlist to the engine (system.py will securely filter this)
os.environ["ORT_AUTO_PROVIDERS"] = ",".join(optimized_providers)

smart_names = [p.replace("ExecutionProvider", "") for p in optimized_providers]
print(f" OS Hardware Acceleration Priority: {' -> '.join(smart_names)}")

# --- .IMPORT APP ---
from app.server import app

def is_port_in_use(port):
    """Reliable socket check - guarantees the window opens the millisecond Uvicorn binds"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def run_server():
    try:
        config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="critical")
        server = uvicorn.Server(config)
        server.run()
    except Exception as e:
        print(f"[ERROR] Server error: {e}")
        sys.exit(1)

def main():
    print("=" * 50)
    print("      LocalReader Plus - Starting")
    print("=" * 50)
    print(f"Project root: {base_dir}")
    
    print("\n[INIT] Starting FastAPI server...")
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    print("[WAIT] Waiting for initialization...")
    retries = 1500  # Give it plenty of time (150 seconds) in case engine load is set to block
    server_up = False
    for attempt in range(1, retries + 1):
        if is_port_in_use(8000):
            server_up = True
            break
        time.sleep(0.1)
        if attempt % 50 == 0:
            print(f"     Still waiting for server port... ({attempt}/{retries})")
            
    if not server_up:
        print("[CRITICAL] Server failed to bind port 8000.")
        sys.exit(1)

    print("[INIT] Creating application window...")
    storage_path = base_dir / 'webview_data'
    
    try:
        window = webview.create_window(
            'LocalReader Plus',
            url='http://127.0.0.1:8000',
            width=1200,
            height=800,
            background_color='#000000',
            min_size=(1000, 700)
        )
        
        print("[OK] Window created successfully")
        print("=" * 50)
        webview.start(debug=False, storage_path=str(storage_path))
        
    except Exception as e:
        print(f"[CRITICAL] Failed to create window: {e}")
        sys.exit(1)

    print("\n[EXIT] Shutting down...")
    os._exit(0)

if __name__ == "__main__":
    main()