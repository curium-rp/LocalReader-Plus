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

# --- 2. SIMPLE DLL INJECTION (EVERY STARTUP) ---
if platform.system() == "Windows":
    print("[STARTUP] Scanning for NVIDIA...")
    found_paths = []
    
    cuda_paths = glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin")
    if cuda_paths:
        cuda_paths.sort(reverse=True)
        best_cuda = cuda_paths[0]
        if os.path.exists(best_cuda):
            found_paths.append(best_cuda)
            
    cudnn_paths = glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin")
    if cudnn_paths:
        cudnn_paths.sort(reverse=True)
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
        
        # Single line print for all linked paths
        print(f"[STARTUP] Linked DLLs -> {' | '.join(found_paths)}")


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
    print("  LocalReader Plus - Starting")
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