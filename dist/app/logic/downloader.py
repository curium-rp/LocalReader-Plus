import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

# =============================================================================
# Section 1: Model Catalog & Metadata
# =============================================================================

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


@dataclass
class ModelBundle:
    id: str
    name: str
    precision: str
    subfolder: str
    model_filename: str
    voices_filename: str
    model_url: str
    voices_url: str
    languages: List[str]
    voice_count: int
    model_size_mb: int
    voices_size_mb: int
    description: str

    @property
    def total_size_mb(self) -> int:
        return self.model_size_mb + self.voices_size_mb

    def get_bundle_dir(self) -> Path:
        if self.subfolder:
            return MODELS_DIR / self.subfolder
        return MODELS_DIR

    def get_model_path(self) -> Path:
        return self.get_bundle_dir() / self.model_filename

    def get_voices_path(self) -> Path:
        return self.get_bundle_dir() / self.voices_filename

    def is_model_installed(self) -> bool:
        p = self.get_model_path()
        return p.is_file() and p.stat().st_size > 1000

    def is_voices_installed(self) -> bool:
        p = self.get_voices_path()
        return p.is_file() and p.stat().st_size > 1000

    def is_installed(self) -> bool:
        return self.is_model_installed() and self.is_voices_installed()

    def get_disk_size_mb(self) -> float:
        total = 0
        m_path = self.get_model_path()
        if m_path.is_file():
            total += m_path.stat().st_size
        v_path = self.get_voices_path()
        if v_path.is_file():
            total += v_path.stat().st_size
        return round(total / (1024 * 1024), 1)


MODEL_CATALOG: Dict[str, ModelBundle] = {
    "kokoro-v1.0": ModelBundle(
        id="kokoro-v1.0",
        name="Kokoro v1.0 (FP32)",
        precision="FP32",
        subfolder="",
        model_filename="kokoro.onnx",
        voices_filename="voices.bin",
        model_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
        voices_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
        languages=["English", "Japanese", "Chinese", "Spanish", "French"],
        voice_count=54,
        model_size_mb=326,
        voices_size_mb=28,
        description="Recommended: Best audio quality, stability, and multilingual support",
    ),
    "kokoro-v1.0-int8": ModelBundle(
        id="kokoro-v1.0-int8",
        name="Kokoro v1.0 (INT8 Quantized)",
        precision="INT8",
        subfolder="",
        model_filename="kokoro.int8.onnx",
        voices_filename="voices.bin",
        model_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx",
        voices_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
        languages=["English", "Japanese", "Chinese", "Spanish", "French"],
        voice_count=54,
        model_size_mb=114,
        voices_size_mb=28,
        description="Quantized model for low memory. Note: Requires AVX-512 VNNI CPU support for full speed",
    ),
    "kokoro-v1.1": ModelBundle(
        id="kokoro-v1.1",
        name="Kokoro v1.1-zh (FP32)",
        precision="FP32",
        subfolder="kokoro-v1.1",
        model_filename="kokoro-v1.1-zh.onnx",
        voices_filename="voices-v1.1-zh.bin",
        model_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.1-zh.onnx",
        voices_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.1-zh.bin",
        languages=["Chinese (Mandarin)", "English"],
        voice_count=103,
        model_size_mb=326,
        voices_size_mb=53,
        description="Chinese & English model with 100 Chinese voices & 3 English voices (Optional)",
    ),
}


def get_bundle(bundle_id: str) -> Optional[ModelBundle]:
    return MODEL_CATALOG.get(bundle_id)


def resolve_bundle_paths(bundle_id: str) -> Tuple[Optional[Path], Optional[Path]]:
    bundle = get_bundle(bundle_id)
    if not bundle:
        return None, None
    return bundle.get_model_path(), bundle.get_voices_path()


# =============================================================================
# Section 2: Catalog Summary & Disk Management
# =============================================================================

def get_default_installed_bundle_id() -> Optional[str]:
    """Find the default installed model bundle, prioritizing kokoro-v1.0."""
    for candidate_id in ["kokoro-v1.0", "kokoro-v1.0-int8", "kokoro-v1.1"]:
        bundle = MODEL_CATALOG[candidate_id]
        if bundle.is_installed():
            return candidate_id
    return None


def get_catalog_summary(active_bundle_id: Optional[str] = None) -> List[dict]:
    items = []
    for b_id, b in MODEL_CATALOG.items():
        installed = b.is_installed()
        m_installed = b.is_model_installed()
        v_installed = b.is_voices_installed()

        # Calculate actual download size needed right now
        needed_download_mb = 0
        if not m_installed:
            needed_download_mb += b.model_size_mb
        if not v_installed:
            needed_download_mb += b.voices_size_mb

        items.append({
            "id": b.id,
            "name": b.name,
            "precision": b.precision,
            "subfolder": b.subfolder,
            "languages": b.languages,
            "voice_count": b.voice_count,
            "model_size_mb": b.model_size_mb,
            "voices_size_mb": b.voices_size_mb,
            "total_size_mb": b.total_size_mb,
            "download_size_mb": needed_download_mb,
            "voices_already_installed": v_installed,
            "disk_size_mb": b.get_disk_size_mb(),
            "description": b.description,
            "installed": installed,
            "is_active": (installed and b_id == active_bundle_id),
            "model_path": str(b.get_model_path()),
            "voices_path": str(b.get_voices_path()),
        })
    return items


def delete_bundle_files(bundle_id: str) -> Tuple[bool, str]:
    bundle = get_bundle(bundle_id)
    if not bundle:
        return False, f"Unknown model bundle: {bundle_id}"

    deleted_count = 0
    freed_bytes = 0

    m_path = bundle.get_model_path()
    if m_path.is_file():
        freed_bytes += m_path.stat().st_size
        m_path.unlink()
        deleted_count += 1

    # Delete voices file, but protect shared voices.bin used by root bundles
    if bundle.subfolder:
        v_path = bundle.get_voices_path()
        if v_path.is_file():
            freed_bytes += v_path.stat().st_size
            v_path.unlink()
            deleted_count += 1
        bundle_dir = bundle.get_bundle_dir()
        try:
            if bundle_dir.is_dir() and not any(bundle_dir.iterdir()):
                bundle_dir.rmdir()
        except Exception:
            pass
    else:
        # For root bundle (e.g. kokoro-v1.0 or kokoro-v1.0-int8),
        # delete voices.bin if no other root bundle model is installed
        other_root_models = [
            b for b in MODEL_CATALOG.values()
            if not b.subfolder and b.id != bundle_id and b.get_model_path().is_file()
        ]
        if not other_root_models:
            v_path = bundle.get_voices_path()
            if v_path.is_file():
                freed_bytes += v_path.stat().st_size
                v_path.unlink()
                deleted_count += 1

    freed_mb = round(freed_bytes / (1024 * 1024), 1)
    return True, f"Deleted {deleted_count} files, freed {freed_mb} MB"


def check_model_exists(bundle_id: str) -> bool:
    """Check if a specific model bundle is installed."""
    if bundle_id == "gpu":
        bundle_id = "kokoro-v1.0"
    elif bundle_id == "cpu":
        bundle_id = "kokoro-v1.0-int8"

    bundle = get_bundle(bundle_id)
    if not bundle:
        return False
    return bundle.is_installed()


def get_available_models() -> dict:
    """Return dictionary of installed models, preserving legacy keys."""
    v1_bundle = get_bundle("kokoro-v1.0")
    int8_bundle = get_bundle("kokoro-v1.0-int8")
    v11_bundle = get_bundle("kokoro-v1.1")

    gpu_installed = v1_bundle.is_installed() if v1_bundle else False
    cpu_installed = int8_bundle.is_installed() if int8_bundle else False
    v11_installed = v11_bundle.is_installed() if v11_bundle else False

    return {
        "kokoro-v1.0": gpu_installed,
        "kokoro-v1.0-int8": cpu_installed,
        "kokoro-v1.1": v11_installed,
        "gpu": gpu_installed,
        "cpu": cpu_installed,
        "voices": (MODELS_DIR / "voices.bin").is_file(),
    }


# =============================================================================
# Section 3: Network Download Engine
# =============================================================================

def classify_network_error(e: Exception) -> str:
    """Classify exceptions into clean, human-readable network status messages."""
    err_str = str(e).lower()

    # 1. Timeouts (check before ConnectionError since ConnectTimeout inherits from both)
    if isinstance(e, requests.exceptions.Timeout) or "timed out" in err_str:
        return "Internet connection unstable (timed out). Please check your connection."

    # 2. Connection dropped / interrupted stream
    if (
        "chunkedencodingerror" in err_str
        or "connection reset" in err_str
        or "incompleteread" in err_str
        or "connection broken" in err_str
        or "connection aborted" in err_str
        or "remotedisconnected" in err_str
    ):
        return "Internet connection unstable (download interrupted). Please try again."

    # 3. DNS failure, host unreachable, no internet connection
    if (
        isinstance(e, requests.exceptions.ConnectionError)
        or "getaddrinfo failed" in err_str
        or "name or service not known" in err_str
        or "failed to establish a new connection" in err_str
        or "network is unreachable" in err_str
        or "nodename nor servname provided" in err_str
        or "no route to host" in err_str
    ):
        return "No internet connection. Please check your network and try again."

    # 4. HTTP response status codes
    if isinstance(e, requests.exceptions.HTTPError):
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code == 429:
            return "Download rate limit reached. Please wait a few minutes."
        elif code:
            return f"Server error ({code}) while downloading. Please try again later."

    return f"Download error: {str(e)}"


def _download_file(
    url: str,
    dest_path: Path,
    label: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".download")

    print(f"Downloading {label}...")
    print(f"  Source: {url}")
    print(f"  Destination: {dest_path}")

    try:
        # 15s connect timeout (fails fast if no internet), 600s chunk read timeout
        response = requests.get(url, stream=True, timeout=(15, 600))
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        dl_mb = downloaded / (1024 * 1024)
                        print(
                            f"  Progress: {pct:.1f}% ({dl_mb:.1f}/{total_mb:.1f} MB)",
                            end="\r",
                        )

        print(f"\n  Finished downloading {label}")
        temp_path.replace(dest_path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        print(f"Download failed for {label}: {e}")
        raise


def download_model_bundle(
    bundle_id: str,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> None:
    """
    Download a model bundle including both its ONNX weights and voice pack.
    Skips voices.bin when a shared pack is already present (v1.0 / INT8).
    """
    if bundle_id == "gpu":
        bundle_id = "kokoro-v1.0"
    elif bundle_id == "cpu":
        bundle_id = "kokoro-v1.0-int8"

    bundle = get_bundle(bundle_id)
    if not bundle:
        raise ValueError(f"Unknown model bundle ID: {bundle_id}")

    print(f"\n--- Kokoro Model Bundle Downloader ---")
    print(f"Bundle: {bundle.name} ({bundle.id})")
    print(f"Target Directory: {bundle.get_bundle_dir()}\n")

    model_path = bundle.get_model_path()
    voices_path = bundle.get_voices_path()

    # 1. Download Model Weights
    if not (model_path.is_file() and model_path.stat().st_size > 1000):
        if model_path.is_file():
            try:
                model_path.unlink()
            except Exception:
                pass

        def on_model_progress(dl, tot):
            if progress_callback:
                progress_callback("model", dl, tot)

        _download_file(
            url=bundle.model_url,
            dest_path=model_path,
            label=f"{bundle.name} model weights",
            progress_callback=on_model_progress,
        )
    else:
        print(f"Model file already exists ({model_path.stat().st_size / (1024*1024):.1f} MB): {model_path.name}")

    # 2. Download Voices Pack (skips if shared pack already present, e.g. kokoro-v1.0 & int8 voices.bin)
    if not (voices_path.is_file() and voices_path.stat().st_size > 1000):
        if voices_path.is_file():
            try:
                voices_path.unlink()
            except Exception:
                pass

        def on_voices_progress(dl, tot):
            if progress_callback:
                progress_callback("voices", dl, tot)

        _download_file(
            url=bundle.voices_url,
            dest_path=voices_path,
            label=f"{bundle.name} voices pack",
            progress_callback=on_voices_progress,
        )
    else:
        print(f"Voices file already exists ({voices_path.stat().st_size / (1024*1024):.1f} MB): {voices_path.name} (shared pack, skipping download)")

    print(f"\nBundle {bundle.id} is ready for use.")


def download_kokoro_model(model_type: str = "kokoro-v1.1") -> None:
    download_model_bundle(model_type)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "kokoro-v1.1"
    download_kokoro_model(target)
