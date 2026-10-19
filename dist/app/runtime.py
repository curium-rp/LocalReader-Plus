"""Explicit launch configuration, independent of the reader's storage paths."""

from dataclasses import dataclass
import os
from pathlib import Path
from .logic.local_server import pick_host


@dataclass(frozen=True)
class ReaderRuntime:
    host: str
    submodule: bool
    models_dir: Path | None

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        mode = env.get("LOCALREADER_MODE", "standalone")
        if mode not in ("standalone", "falcotype"):
            raise ValueError("Unknown LocalReader launch mode.")
        submodule = mode == "falcotype"
        host = env.get("LOCALREADER_HOST", pick_host(submodule))
        if host not in ("127.0.0.1", "127.77.77.77"):
            raise ValueError("LocalReader must use a supported loopback address.")
        models_dir = None
        if submodule:
            value = env.get("FALCOTYPE_MODELS_DIR", "")
            if not value or not Path(value).is_absolute():
                raise ValueError("FalcoType must provide an absolute FALCOTYPE_MODELS_DIR.")
            models_dir = Path(value).resolve()
        return cls(host, submodule, models_dir)


runtime = ReaderRuntime.from_environment()
