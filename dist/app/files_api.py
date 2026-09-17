import os
import sys
import json
import time
import uuid
import asyncio
import subprocess
import platform
from pathlib import Path
from typing import Optional, Dict, Any, List
try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from pydantic import BaseModel
    router = APIRouter(prefix="/api/explorer", tags=["explorer"])
except ImportError:
    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = ""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
    class BaseModel:
        pass
    router = None

from .config import userdata_dir, master_tree_file, tree_dir, columepx_file
from .utils import safe_save_json
from .logic.memories import (
    call_peeker_manifest,
    intercept_manifest,
    find_peek_id_by_path,
    peek_catalog_item,
    load_book_info,
)


def format_file_size(size_in_bytes: int) -> str:
    """Format bytes to human readable string (KB, MB, GB)."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.1f} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"


def pick_folder_native() -> str:
    """
    Open native folder dialog across Windows, macOS, and Linux.
    - Windows: Tkinter (topmost) -> PowerShell FolderBrowserDialog.
    - macOS: AppleScript (osascript choose folder) -> Tkinter.
    - Linux: zenity -> kdialog -> Tkinter.
    """
    system = platform.system().lower()

    # 1. macOS Native Dialog via osascript
    if system == "darwin":
        try:
            cmd = [
                "osascript",
                "-e",
                'tell application (path to frontmost application as text) to set myFolder to choose folder with prompt "Select Book Folder"',
                "-e",
                "POSIX path of myFolder",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = proc.stdout.strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception as e:
            print(f"[Manager] macOS AppleScript dialog failed: {e}")

        try:
            cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select Book Folder")']
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = proc.stdout.strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception as e:
            print(f"[Manager] macOS simple osascript failed: {e}")

    # 2. Linux Native Dialog via zenity or kdialog
    elif system == "linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return ""

        # Try Zenity (GNOME / GTK standard)
        try:
            proc = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title=Select Book Folder"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = proc.stdout.strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception as e:
            print(f"[Manager] Linux zenity dialog failed: {e}")

        # Try Kdialog (KDE / Qt standard)
        try:
            proc = subprocess.run(
                ["kdialog", "--getexistingdirectory", ".", "--title", "Select Book Folder"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = proc.stdout.strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception as e:
            print(f"[Manager] Linux kdialog dialog failed: {e}")

    # 3. Cross-Platform Tkinter (Windows, macOS, Linux fallback)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select Book Folder")
        root.destroy()
        if selected:
            return str(Path(selected).resolve())
    except Exception as e:
        print(f"[Manager] Tkinter dialog failed: {e}")

    # 4. Windows PowerShell Fallback
    if system == "windows":
        try:
            ps_script = """
            Add-Type -AssemblyName System.Windows.Forms
            $f = New-Object System.Windows.Forms.FolderBrowserDialog
            $f.Description = 'Select Book Folder'
            $f.ShowNewFolderButton = $false
            if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
                Write-Output $f.SelectedPath
            }
            """
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = proc.stdout.strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception as e:
            print(f"[Manager] PowerShell dialog failed: {e}")

    return ""


IGNORED_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "node_modules",
    ".git",
    ".gemini",
    "__pycache__",
    ".vscode",
    ".idea",
}


def scan_folder_tree(root_path: Path) -> Dict[str, Any]:
    """
    Recursively scans the folder hierarchy for .epub files (mimicking tree /f).
    The root folder is the top of the tree.
    Returns both hierarchical tree structure and flat file list for instant searching.
    """
    root_path = root_path.resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise ValueError(f"Directory does not exist or is not a folder: {root_path}")

    flat_list: List[Dict[str, Any]] = []

    def _walk_dir(current_dir: Path) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "name": current_dir.name,
            "path": str(current_dir),
            "is_dir": True,
            "type": "File folder",
            "subfolders": [],
            "files": [],
            "book_count": 0,
            "size_bytes": 0,
            "size_formatted": "",
        }

        try:
            entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError) as err:
            print(f"[Manager] Access denied skipping: {current_dir} ({err})")
            return node

        for entry in entries:
            # Skip hidden and system folders
            if entry.name.startswith(".") or entry.name.lower() in IGNORED_DIR_NAMES:
                continue

            if entry.is_dir():
                sub_node = _walk_dir(entry)
                # Keep subfolders even if empty or containing books
                node["subfolders"].append(sub_node)
                node["book_count"] += sub_node["book_count"]
            elif entry.is_file():
                if entry.suffix.lower() == ".epub":
                    try:
                        stat = entry.stat()
                        size_bytes = stat.st_size
                        mtime = stat.st_mtime
                    except Exception:
                        size_bytes = 0
                        mtime = 0.0

                    file_info = {
                        "name": entry.name,
                        "title": entry.stem,
                        "path": str(entry),
                        "is_dir": False,
                        "type": "EPUB",
                        "size_bytes": size_bytes,
                        "size_formatted": format_file_size(size_bytes),
                        "mtime": mtime,
                        "ext": ".epub",
                    }
                    node["files"].append(file_info)
                    node["book_count"] += 1

                    try:
                        rel_path = str(entry.relative_to(root_path))
                    except ValueError:
                        rel_path = entry.name

                    flat_list.append({
                        "name": entry.name,
                        "title": entry.stem,
                        "path": str(entry),
                        "rel_path": rel_path,
                        "is_dir": False,
                        "type": "EPUB",
                        "size_bytes": size_bytes,
                        "size_formatted": format_file_size(size_bytes),
                        "mtime": mtime,
                    })

        return node

    tree_node = _walk_dir(root_path)

    tree_data = {
        "root_path": str(root_path),
        "root_name": root_path.name or str(root_path),
        "scanned_at": time.time(),
        "total_books": len(flat_list),
        "tree": tree_node,
        "flat_list": flat_list,
    }

    return tree_data


def load_master_tree() -> Dict[str, Any]:
    """Load userdata/tree/master_tree.json or return an empty template."""
    if master_tree_file.exists():
        try:
            with open(master_tree_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "paths" in data:
                    for p in data["paths"]:
                        if "collapsed" not in p:
                            p["collapsed"] = False
                    return data
        except Exception as e:
            print(f"[FilesAPI] Error reading master_tree.json: {e}")

    return {"active_id": None, "paths": []}


def save_master_tree(data: Dict[str, Any]) -> None:
    """Save master paths registry to userdata/tree/master_tree.json."""
    safe_save_json(master_tree_file, data, indent=2)


def get_tree_file_for_id(path_id: str) -> Path:
    """Return Path for userdata/tree/tree_<id>.json."""
    clean_id = "".join(c for c in str(path_id) if c.isalnum() or c in ("-", "_"))
    return tree_dir / f"tree_{clean_id}.json"



def load_tree_log(path_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Load tree structure for a given path_id, or the currently active path in master_tree.
    """
    master = load_master_tree()
    target_id = path_id or master.get("active_id")

    if target_id:
        p_file = get_tree_file_for_id(target_id)
        if p_file.exists():
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data["id"] = str(target_id)
                        for p in master.get("paths", []):
                            if str(p.get("id")) == str(target_id):
                                data["collapsed"] = p.get("collapsed", False)
                                break
                    return data
            except Exception as e:
                print(f"[FilesAPI] Error reading {p_file.name}: {e}")

    return {
        "root_path": "",
        "root_name": "",
        "scanned_at": 0,
        "total_books": 0,
        "tree": None,
        "flat_list": [],
    }


def stamp_tree_library(node: Any, path_id: str, root_name: str) -> None:
    """Stamp path_id, root_name, and type onto nested folders/files without a rescan."""
    if not isinstance(node, dict):
        return
    node["path_id"] = path_id
    node["root_name"] = root_name
    if node.get("is_dir") is not False:
        node.setdefault("type", "File folder")
        node.setdefault("size_bytes", 0)
        node.setdefault("size_formatted", "")
    for sub in node.get("subfolders") or []:
        stamp_tree_library(sub, path_id, root_name)
    for book in node.get("files") or []:
        if not isinstance(book, dict):
            continue
        book["path_id"] = path_id
        book["root_name"] = root_name
        book.setdefault("is_dir", False)
        book.setdefault("type", "EPUB")


def load_all_trees() -> Dict[str, Any]:
    """
    Loads all registered paths from master_tree.json and their respective tree structures.
    Returns combined multi-root trees, aggregated book count, and unified flat list.
    """
    master = load_master_tree()
    paths = master.get("paths", [])
    trees: List[Dict[str, Any]] = []
    total_books = 0
    flat_list_all: List[Dict[str, Any]] = []

    for p in paths:
        pid = str(p.get("id"))
        p_file = get_tree_file_for_id(pid)
        tree_node = None
        p_flat_list: List[Dict[str, Any]] = []
        scanned_at = p.get("scanned_at") or 0

        if p_file.exists():
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
                    if isinstance(tdata, dict):
                        tree_node = tdata.get("tree")
                        p_flat_list = tdata.get("flat_list", [])
                        scanned_at = tdata.get("scanned_at") or scanned_at
            except Exception as e:
                print(f"[FilesAPI] Error reading {p_file.name}: {e}")
        else:
            p_path_str = p.get("path")
            if p_path_str:
                try:
                    p_path_obj = Path(p_path_str)
                    if p_path_obj.exists() and p_path_obj.is_dir():
                        tdata = scan_folder_tree(p_path_obj)
                        save_tree_log(tdata, pid)
                        tree_node = tdata.get("tree")
                        p_flat_list = tdata.get("flat_list", [])
                        scanned_at = tdata.get("scanned_at") or time.time()
                except Exception as e:
                    print(f"[FilesAPI] Auto-scan failed for {p_path_str}: {e}")

        root_name = p.get("name") or Path(p.get("path", "")).name
        stamp_tree_library(tree_node, pid, root_name)

        # Tag each item in p_flat_list with path_id and root_name for multi-root searching
        for book in p_flat_list:
            b_copy = dict(book)
            b_copy["path_id"] = pid
            b_copy["root_name"] = root_name
            b_copy.setdefault("is_dir", False)
            b_copy.setdefault("type", "EPUB")
            flat_list_all.append(b_copy)

        p_books = p.get("total_books") if p.get("total_books") is not None else len(p_flat_list)
        total_books += p_books

        trees.append({
            "id": pid,
            "name": root_name,
            "path": p.get("path", ""),
            "total_books": p_books,
            "collapsed": p.get("collapsed", False),
            "scanned_at": scanned_at,
            "tree": tree_node,
            "flat_list": p_flat_list,
        })

    return {
        "active_id": master.get("active_id"),
        "paths": paths,
        "trees": trees,
        "total_books": total_books,
        "flat_list": flat_list_all,
    }


def rescan_all_paths() -> Dict[str, Any]:
    """
    Rescans all registered paths and updates individual tree logs and master_tree.json.
    """
    master = load_master_tree()
    for p in master.get("paths", []):
        p_path_str = p.get("path")
        if p_path_str:
            p_path = Path(p_path_str)
            if p_path.exists() and p_path.is_dir():
                try:
                    tree_data = scan_folder_tree(p_path)
                    save_tree_log(tree_data, p.get("id"))
                except Exception as e:
                    print(f"[FilesAPI] Error rescanning path {p_path_str}: {e}")
    return load_all_trees()


def open_path_in_os(target_path: str) -> bool:
    """Opens a file or folder in the OS native file explorer."""
    path_obj = Path(target_path).resolve()
    if not path_obj.exists():
        if path_obj.parent.exists():
            path_obj = path_obj.parent
        else:
            return False

    sys_name = platform.system().lower()
    try:
        if sys_name == "windows":
            if path_obj.is_file():
                subprocess.Popen(f'explorer /select,"{path_obj}"', shell=True)
            else:
                os.startfile(str(path_obj))
            return True
        elif sys_name == "darwin":
            subprocess.Popen(["open", "-R" if path_obj.is_file() else "", str(path_obj)])
            return True
        else:
            subprocess.Popen(["xdg-open", str(path_obj if path_obj.is_dir() else path_obj.parent)])
            return True
    except Exception as e:
        print(f"[FilesAPI] Failed to open path in OS: {e}")
        return False


def save_tree_log(tree_data: Dict[str, Any], path_id: Optional[str] = None) -> str:
    """
    Save tree structure to userdata/tree_<id>.json and register in master_tree.json.
    Returns the path_id.
    """
    master = load_master_tree()
    target_path = str(tree_data.get("root_path", "")).strip()

    matched_entry = None
    if path_id:
        for p in master["paths"]:
            if str(p.get("id")) == str(path_id):
                matched_entry = p
                break

    if not matched_entry and target_path:
        for p in master["paths"]:
            try:
                if Path(p.get("path", "")).resolve() == Path(target_path).resolve():
                    matched_entry = p
                    break
            except Exception:
                if p.get("path", "").lower() == target_path.lower():
                    matched_entry = p
                    break

    if matched_entry:
        assigned_id = str(matched_entry["id"])
        matched_entry["path"] = target_path
        matched_entry["name"] = tree_data.get("root_name") or Path(target_path).name
        matched_entry["scanned_at"] = tree_data.get("scanned_at") or time.time()
        matched_entry["total_books"] = tree_data.get("total_books") or 0
        matched_entry["file"] = f"tree_{assigned_id}.json"
    else:
        existing_ids = [int(p["id"]) for p in master["paths"] if str(p.get("id", "")).isdigit()]
        next_num = max(existing_ids) + 1 if existing_ids else 1
        assigned_id = str(next_num)
        new_entry = {
            "id": assigned_id,
            "file": f"tree_{assigned_id}.json",
            "path": target_path,
            "name": tree_data.get("root_name") or Path(target_path).name,
            "scanned_at": tree_data.get("scanned_at") or time.time(),
            "total_books": tree_data.get("total_books") or 0,
            "collapsed": False,
        }
        master["paths"].append(new_entry)

    master["active_id"] = assigned_id
    save_master_tree(master)

    target_file = get_tree_file_for_id(assigned_id)
    tree_data["id"] = assigned_id
    safe_save_json(target_file, tree_data, indent=2)

    return assigned_id


def delete_tree_path(path_id: str) -> Dict[str, Any]:
    """
    Deletes path entry from master_tree.json and removes tree_<id>.json.
    If the active path was deleted, sets active_id to next available or None.
    """
    master = load_master_tree()
    target_id = str(path_id)

    target_file = get_tree_file_for_id(target_id)
    if target_file.exists():
        try:
            target_file.unlink(missing_ok=True)
        except Exception as e:
            print(f"[Manager] Failed to remove {target_file}: {e}")

    master["paths"] = [p for p in master["paths"] if str(p.get("id")) != target_id]

    if str(master.get("active_id")) == target_id:
        master["active_id"] = master["paths"][0]["id"] if master["paths"] else None

    save_master_tree(master)
    new_active_tree = load_tree_log(master.get("active_id")) if master.get("active_id") else None

    return {
        "status": "ok",
        "deleted_id": target_id,
        "active_id": master.get("active_id"),
        "master": master,
        "active_tree": new_active_tree,
        "all_trees": load_all_trees(),
    }



def switch_active_path(path_id: str) -> Dict[str, Any]:
    """Switches active path in master_tree.json and returns its tree data."""
    master = load_master_tree()
    target_id = str(path_id)
    found = any(str(p.get("id")) == target_id for p in master.get("paths", []))
    if not found:
        raise HTTPException(status_code=404, detail=f"Path ID {path_id} not found in master_tree")

    master["active_id"] = target_id
    save_master_tree(master)

    tree_data = load_tree_log(target_id)
    return {"status": "ok", "active_id": target_id, "data": tree_data}


def save_paths_order(ordered_paths: List[Dict[str, Any]], active_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Saves the user-arranged or A-Z sorted list of paths into master_tree.json.
    """
    master = load_master_tree()
    master["paths"] = ordered_paths
    if active_id is not None:
        master["active_id"] = str(active_id) if active_id else None
    elif master.get("active_id") not in [str(p.get("id")) for p in ordered_paths]:
        master["active_id"] = ordered_paths[0]["id"] if ordered_paths else None

    save_master_tree(master)
    return {"status": "ok", "master": master}


def add_book_to_shelf(epub_path_str: str) -> Dict[str, Any]:
    """
    Registers an external EPUB path as userdata/library/<id>/info.json (no library.json row).
    """
    epub_path = Path(epub_path_str).resolve()
    if not epub_path.exists() or not epub_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {epub_path_str}")

    if epub_path.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="Only .epub files are supported for path binding")

    target_str = str(epub_path)
    existing_id = find_peek_id_by_path(target_str)
    if existing_id:
        info = load_book_info(existing_id) or {}
        existing = peek_catalog_item(existing_id) or {
            "id": existing_id,
            "fileName": info.get("fileName") or epub_path.stem,
            "title": info.get("title") or epub_path.stem,
        }
        return {"status": "already_in_shelf", "book": existing, "is_new": False}

    doc_id = str(uuid.uuid4())
    book_title = epub_path.stem

    try:
        cpp_manifest = call_peeker_manifest(epub_path)
        meta = intercept_manifest(cpp_manifest, epub_path, doc_id=doc_id)
        book_title = meta.get("title") or meta.get("fileName") or book_title
    except Exception as e:
        print(f"[Manager] Manifest intercept on add-to-shelf failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to index EPUB: {e}")

    new_book = peek_catalog_item(doc_id) or {
        "id": doc_id,
        "fileName": book_title,
        "title": book_title,
    }
    return {"status": "ok", "book": new_book, "is_new": True}


# --- API Models ---
class PathPayload(BaseModel):
    path: str
    id: Optional[str] = None


class AddToShelfPayload(BaseModel):
    epub_path: str


class SwitchPathPayload(BaseModel):
    id: str


class SavePathsPayload(BaseModel):
    paths: List[Dict[str, Any]]
    active_id: Optional[str] = None


class CollapsePayload(BaseModel):
    id: str
    collapsed: bool


class OpenPathPayload(BaseModel):
    path: str


class ColumnUpdateRequest(BaseModel):
    view: str
    widths: Dict[str, Any]


def read_columepx() -> Dict[str, Any]:
    if columepx_file.exists():
        try:
            with open(columepx_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_columepx_data(view: str, widths: Dict[str, Any]) -> Dict[str, Any]:
    current = read_columepx()
    current[view] = widths
    columepx_file.parent.mkdir(parents=True, exist_ok=True)
    safe_save_json(columepx_file, current, indent=2)
    return current


# --- Router Endpoints ---
if router is not None:
    @router.post("/pick-folder")
    async def api_pick_folder():
        """Trigger the native OS folder picker dialog."""
        selected = await asyncio.to_thread(pick_folder_native)
        return {"path": selected}

    @router.get("/paths")
    async def api_get_paths():
        """Return master_tree.json containing all registered book paths."""
        return load_master_tree()

    @router.post("/paths/save")
    async def api_save_paths(payload: SavePathsPayload):
        """Save reordered or sorted list of paths."""
        return save_paths_order(payload.paths, payload.active_id)

    @router.post("/paths/collapse")
    async def api_collapse_path(payload: CollapsePayload):
        """Update and persist collapsed status for a master path."""
        master = load_master_tree()
        target_id = str(payload.id)
        matched = False
        for p in master.get("paths", []):
            if str(p.get("id")) == target_id:
                p["collapsed"] = bool(payload.collapsed)
                matched = True
                break
        if matched:
            save_master_tree(master)
        return {"status": "ok", "id": target_id, "collapsed": bool(payload.collapsed), "master": master}

    @router.post("/switch-path")
    async def api_switch_path(payload: SwitchPathPayload):
        """Switch the active path in the master registry."""
        return switch_active_path(payload.id)

    @router.delete("/path/{path_id}")
    async def api_delete_path(path_id: str):
        """Delete a path from the master registry and remove its tree file."""
        return delete_tree_path(path_id)

    @router.post("/scan")
    async def api_scan_folder(payload: PathPayload):
        """Scan directory path and write tree structure to userdata/tree_<id>.json + master_tree.json."""
        folder_path = Path(payload.path.strip()).resolve()
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Invalid folder path: {payload.path}")

        try:
            tree_data = await asyncio.to_thread(scan_folder_tree, folder_path)
            assigned_id = save_tree_log(tree_data, payload.id)
            return {
                "status": "ok",
                "id": assigned_id,
                "data": tree_data,
                "all_trees": load_all_trees(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to scan directory: {str(e)}")

    @router.get("/trees")
    async def api_get_all_trees():
        """Return all registered book trees and aggregated metadata."""
        data = load_all_trees()
        return {"status": "ok", "data": data}

    @router.get("/tree")
    async def api_get_tree(id: Optional[str] = None, all: Optional[bool] = False):
        """Return tree structure. If all=true or no id, returns all trees; otherwise single tree."""
        if id and not all:
            data = load_tree_log(id)
            return {"status": "ok", "data": data}
        data = load_all_trees()
        return {"status": "ok", "data": data}

    @router.post("/rescan-all")
    async def api_rescan_all():
        """Rescan all registered book folder paths."""
        data = await asyncio.to_thread(rescan_all_paths)
        return {"status": "ok", "data": data}

    @router.post("/open-in-os")
    async def api_open_in_os(payload: OpenPathPayload):
        """Open a directory or file in the native OS file explorer."""
        success = await asyncio.to_thread(open_path_in_os, payload.path)
        if not success:
            raise HTTPException(status_code=404, detail="Path does not exist on disk")
        return {"status": "ok", "path": payload.path}

    @router.post("/add-to-shelf")
    async def api_add_to_shelf(payload: AddToShelfPayload):
        """1-Click Add book path to active library shelf."""
        try:
            result = add_book_to_shelf(payload.epub_path)
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to add book to shelf: {str(e)}")

    @router.get("/read-file")
    async def api_read_file(path: str):
        """Read a local file as response (e.g. for dropped PDF processing)."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        from fastapi.responses import FileResponse
        return FileResponse(str(p))

    @router.get("/columepx")
    async def api_get_columepx():
        """Return stored column pixel widths from userdata/log/columepx.json."""
        return read_columepx()

    @router.post("/columepx")
    async def api_save_columepx(payload: ColumnUpdateRequest):
        """Update column pixel widths for a view and persist to disk."""
        data = save_columepx_data(payload.view, payload.widths)
        return {"status": "ok", "data": data}

