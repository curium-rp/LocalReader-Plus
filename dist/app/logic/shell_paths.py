"""Open a path in the native file manager without importing reader state."""
import os
import platform
import subprocess
from pathlib import Path


def open_path_in_os(target_path: str) -> bool:
    path = Path(target_path).resolve()
    if not path.exists():
        path = path.parent
    if not path.exists():
        return False
    try:
        system = platform.system().lower()
        if system == "windows":
            if path.is_file():
                subprocess.Popen(["explorer.exe", f"/select,{path}"], shell=False)
            else:
                os.startfile(str(path))
        elif system == "darwin":
            subprocess.Popen(["open", *(["-R"] if path.is_file() else []), str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
        return True
    except OSError:
        return False
