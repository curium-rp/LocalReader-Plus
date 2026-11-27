"""Optional language packages (Japanese, Chinese) for the reader's pipelines.

Standard library only and no reader imports: FalcoType loads this file by path
to list and install the packages into the Python the reader really runs on.
A standalone reader installs everything up front and never shows this.
"""

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# uv reads the reader's own uv.toml from here.
DIST = Path(__file__).resolve().parents[2]

# pip name -> import name. jaconv is shared: installing Japanese also satisfies Chinese.
PACKS = {
    "ja": {
        "name": "Japanese",
        "description": "Reads Japanese text aloud: word splitting, readings and pitch accents.",
        "packages": {"fugashi": "fugashi", "unidic-lite": "unidic_lite", "jaconv": "jaconv", "jamdict": "jamdict"},
    },
    "zh": {
        "name": "Chinese",
        "description": "Reads Chinese (Mandarin) text aloud: character width and symbol clean-up.",
        "packages": {"jaconv": "jaconv"},
    },
}
# A package can be present while its dictionary is broken.
PROBES = {"ja": "import fugashi; fugashi.Tagger()"}
MODULES = sorted({module for pack in PACKS.values() for module in pack["packages"].values()})
_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _is_current(python):
    if not python:
        return True
    try:
        return os.path.samefile(python, sys.executable)
    except OSError:
        return False


def missing_modules(python=None):
    """Import names from MODULES that `python` (default: this interpreter) cannot find."""
    if _is_current(python):
        importlib.invalidate_caches()
        return {name for name in MODULES if importlib.util.find_spec(name) is None}
    code = ("import importlib.util, json; "
            f"print(json.dumps([n for n in {MODULES!r} if importlib.util.find_spec(n) is None]))")
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=30, creationflags=_FLAGS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Could not inspect the reader's Python.")
    return set(json.loads(result.stdout))


def pack_states(missing):
    """Each package's state from what is really installed, so shared packages count for both."""
    states = {}
    for pack_id, pack in PACKS.items():
        absent = [name for name, module in pack["packages"].items() if module in missing]
        states[pack_id] = {"state": "installed" if not absent else "missing" if len(absent) == len(pack["packages"]) else "partial",
                           "missing": absent}
    return states


def ready(pack_id):
    """True when this interpreter can run the package's pipeline."""
    return all(importlib.util.find_spec(module) is not None for module in PACKS[pack_id]["packages"].values())


def install_commands(packages, python, which=shutil.which):
    """uv first (no cache), then pip from the target Python (no cache)."""
    commands = []
    uv = which("uv")
    if uv:
        commands.append(("uv", [uv, "pip", "install", "--no-cache", "--python", str(python), *packages]))
    commands.append(("pip", [str(python), "-m", "pip", "install", "--no-cache-dir", *packages]))
    return commands


def run_command(argv, on_line):
    """Run one installer, reporting each output line. Returns its exit code."""
    process = subprocess.Popen(argv, cwd=str(DIST), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=_FLAGS)
    for line in process.stdout:
        if line.strip():
            on_line(line.strip())
    return process.wait()


def run_probe(python, code):
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=120, creationflags=_FLAGS)
    if result.returncode == 0:
        return ""
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else "The package did not start."


class Installer:
    """One install at a time. The job keeps the last result until the next install."""

    def __init__(self, runner=run_command, which=shutil.which, probe=run_probe, inspect=missing_modules):
        self._runner, self._which, self._probe, self._inspect = runner, which, probe, inspect
        self._lock = threading.Lock()
        self.job = {"pack": None, "state": "idle", "tool": "", "line": "", "error": ""}

    def _set(self, **changes):
        with self._lock:
            self.job = {**self.job, **changes}

    def begin(self, pack_id):
        if pack_id not in PACKS:
            raise KeyError(pack_id)
        with self._lock:
            if self.job["state"] == "installing":
                raise RuntimeError("Another language package is installing.")
            self.job = {"pack": pack_id, "state": "installing", "tool": "", "line": "Starting...", "error": ""}

    def install(self, pack_id, python=None):
        """Blocking install of a package begun with begin(). Returns True on success."""
        python = str(python or sys.executable)
        packages = list(PACKS[pack_id]["packages"])
        error = "Neither uv nor pip is available to install packages."
        failures = []
        try:
            for tool, argv in install_commands(packages, python, self._which):
                self._set(tool=tool, line=f"Installing with {tool}...")
                last = [""]

                def on_line(line):
                    last[0] = line
                    self._set(line=line)

                try:
                    code = self._runner(argv, on_line)
                except OSError as failure:
                    error = f"{tool} could not start: {failure}"
                    continue
                if code == 0:
                    error = ""
                    break
                error = last[0] or f"{tool} failed with exit code {code}."
                failures.append(error)
            # The first installer that really ran explains the failure best
            # (a missing pip after a failed uv would hide the real reason).
            if error and failures:
                error = failures[0]
            if not error:
                importlib.invalidate_caches()
                absent = pack_states(self._inspect(python))[pack_id]["missing"]
                if absent:
                    error = "Still missing after install: " + ", ".join(absent)
                elif pack_id in PROBES:
                    error = self._probe(python, PROBES[pack_id])
        except Exception as failure:  # The job must never stay "installing".
            error = str(failure) or failure.__class__.__name__
        self._set(state="failed" if error else "done", error=error, line="" if error else "Installed.")
        return not error

    def start(self, pack_id, python=None):
        self.begin(pack_id)
        threading.Thread(target=self.install, args=(pack_id, python), daemon=True, name="language-pack-install").start()

    def snapshot(self, python=None):
        with self._lock:
            job = dict(self.job)
        states = pack_states(self._inspect(python))
        packs = []
        for pack_id, pack in PACKS.items():
            row = {"id": pack_id, "name": pack["name"], "description": pack["description"],
                   "packages": list(pack["packages"]), **states[pack_id], "line": "", "error": ""}
            if job["pack"] == pack_id and job["state"] == "installing":
                row.update(state="installing", line=job["line"])
            elif job["pack"] == pack_id and job["state"] == "failed":
                # A dictionary that fails its probe is not usable even when every module is present.
                row.update(state="failed", error=job["error"])
            packs.append(row)
        return {"packs": packs, "busy": job["state"] == "installing"}


installer = Installer()
