from pathlib import Path


# CRITICAL: Path Anchoring Functions
def get_app_anchored_path(relative_path: str) -> Path:
    """
    Returns a guaranteed absolute path relative to THIS script file.
    Immune to where the user launched the terminal from.
    """
    # Get the app root (parent of app/ directory)
    # This file is inside dist/app/config.py, so parent is dist/app/, parent.parent is dist/
    script_dir = Path(__file__).parent.absolute()
    app_root = script_dir.parent

    # Join and resolve to absolute path
    return (app_root / relative_path).absolute()


# Developer switch: True enables pywebview dev tools (Ctrl+Shift+C / F12; does not auto-open).
DEBUG = False

# Base directories
base_dir = Path(__file__).parent.absolute()
userdata_dir = get_app_anchored_path("userdata")
content_dir = userdata_dir / "content"
metadata_dir = userdata_dir / "metadata"
library_dir = userdata_dir / "library"
tree_dir = userdata_dir / "tree"
log_dir = userdata_dir / "log"

# File paths
settings_file = userdata_dir / "settings.json"
rules_file = userdata_dir / "pronunciationrules.json"
ignore_file = userdata_dir / "ignore.json"
library_file = userdata_dir / "library.json"
render_file = userdata_dir / "render.json"
view_file = userdata_dir / "view.json"
state_file = userdata_dir / "state.json"
master_tree_file = tree_dir / "master_tree.json"
columepx_file = log_dir / "columepx.json"

# Release identity. Bump APP_VERSION with each GitHub release; the tag must lead
# with the same numbers ("v2.3", "v-2.3") for the update notice to compare.
APP_NAME = "LocalReader Plus"
APP_VERSION = "2.4"
RELEASES_API = "https://api.github.com/repos/curium-rp/LocalReader-Plus/releases/latest"

# Ensure directories exist
try:
    userdata_dir.mkdir(exist_ok=True)
    content_dir.mkdir(exist_ok=True)
    metadata_dir.mkdir(exist_ok=True)
    library_dir.mkdir(exist_ok=True)
    tree_dir.mkdir(exist_ok=True)
    log_dir.mkdir(exist_ok=True)
except Exception as e:
    print(f"[CRITICAL] Failed to create storage dirs: {e}")
