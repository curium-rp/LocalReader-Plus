import os
import sys
import socket
import threading
import time
import uvicorn
import webview
import platform
from pathlib import Path

# --- 1. ARCHITECTURAL SETUP: ABSOLUTE PATH ANCHORING ---
# Anchor all paths to THIS script file location (immune to CWD changes)
base_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(base_dir))

# --- 2. LOCAL FFMPEG & NVIDIA GPU BYPASS SETUP ---
bin_path = base_dir / "bin"

if bin_path.exists():
    # Prepend to PATH for this session only (helps FFMPEG)
    os.environ["PATH"] = str(bin_path) + os.pathsep + os.environ["PATH"]
    
    # THE MAGIC BULLET: Force Windows to grant DLL permissions to this specific folder
    if platform.system() == "Windows":
        try:
            os.add_dll_directory(str(bin_path))
            print(f"[OK] Windows DLL Security Bypass active for: {bin_path}")
        except Exception as e:
            print(f"[WARNING] Could not bypass DLL security: {e}")
            
    print(f"[OK] Local Bin folder linked successfully.")
else:
    print(f"[WARNING] Local 'bin' folder not found at {bin_path}")
    print(f"          FFMPEG and GPU DLLs may not load correctly.")

# --- 3. IMPORT APP ---
# Now when the app loads, Windows has already granted it permission to read the GPU files
from app.server import app

def is_port_in_use(port):
# ... (Keep the rest of your main.py code exactly the same below this line) ... """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def run_server():
    """Runs the FastAPI server in background thread"""
    try:
        config = uvicorn.Config(
            app, 
            host="127.0.0.1", 
            port=8000, 
            log_level="critical"  # Suppress uvicorn logs
        )
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
    
    # 1. Start backend server in daemon thread
    print("\n[INIT] Starting FastAPI server...")
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 2. Wait for server to be responsive (with timeout)
    print("[WAIT] Waiting for server to initialize...")
    retries = 150
    server_up = False
    
    for attempt in range(1, retries + 1):
        if is_port_in_use(8000):
            server_up = True
            print(f"[OK] Server ready on http://127.0.0.1:8000 (attempt {attempt})")
            break
        time.sleep(0.1)
        if attempt % 50 == 0:
            print(f"     Still waiting... ({attempt}/{retries})")

    if not server_up:
        print("[CRITICAL] Server failed to start within 15 seconds")
        print("           Check if port 8000 is already in use:")
        print("           -> netstat -ano | findstr :8000")
        sys.exit(1)

    # 3. Create the main window
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
        print("  LocalReader Plus - Ready!")
        print("=" * 50)
        print()
        
        # 4. Start the UI event loop (blocks until window closes)
        webview.start(debug=False, storage_path=str(storage_path))
        
    except Exception as e:
        print(f"[CRITICAL] Failed to create window: {e}")
        sys.exit(1)

    # 5. Cleanup on exit
    print("\n[EXIT] LocalReader Plus shutting down...")
    os._exit(0)

if __name__ == "__main__":
    main()
