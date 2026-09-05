from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from ..state import export_status, ffmpeg_status
from ..config import content_dir, library_file, userdata_dir
from ..models import ExportRequest, SynthesisRequest
from ..utils import has_onnxruntime_gpu
from .tts import synthesize
from selectolax.parser import HTMLParser, Node
import json
import re
import numpy as np
import os
import platform
import subprocess
import shutil
import soundfile as sf
import sys
import asyncio
import io
from pathlib import Path

# Fix paths for logic imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

try:
    from logic.dependency_manager import FFMPEGInstaller, get_ffmpeg_path
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from dependency_manager import FFMPEGInstaller, get_ffmpeg_path

router = APIRouter()
ffmpeg_installer = None

EXPORT_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
SKIP_SPEECH_TAGS = frozenset({"script", "style", "rt", "rp"})


def _attr(node, key, default=""):
    return (node.attributes or {}).get(key, default) if node else default


def _find_parent(node, tags):
    wanted, p = set(tags), (node.parent if node else None)
    while p:
        if p.tag in wanted: return p
        p = p.parent
    return None


def _element_matches_target_id(node, target_id: Optional[str]) -> bool:
    if not target_id or node is None:
        return False
    if _attr(node, "id") == target_id or _attr(node, "data-orig-id") == target_id:
        return True
    safe = target_id.replace("\\", "\\\\").replace('"', '\\"')
    try:
        return bool(node.css_first(f'#{safe}, [data-orig-id="{safe}"]'))
    except Exception:
        return False


def find_export_elements(tree):
    """Return direct-ID reader blocks and headings/scene-breaks in document order."""
    root = (tree.body or tree.root) if isinstance(tree, HTMLParser) else tree
    if root is None:
        return []
    ordered = []

    def visit(parent: Node) -> None:
        child = parent.child
        while child is not None:
            nxt = child.next
            tag, el_id = child.tag, str(_attr(child, "id"))
            is_reader = el_id.startswith("s_")
            is_head = tag in EXPORT_HEADING_TAGS
            is_scene = tag == "s"
            is_img = tag == "img" and not _find_parent(child, ("p", *EXPORT_HEADING_TAGS))

            if is_reader or is_head or is_scene or is_img:
                ordered.append(child)
            else:
                visit(child)
            child = nxt

    visit(root)
    return ordered


def extract_export_text(element) -> str:
    """Extract narration text while omitting ruby annotations and footnote callouts."""
    if element is None:
        return ""
    parts = []

    def walk(parent: Node) -> None:
        child = parent.child
        while child is not None:
            nxt, tag = child.next, child.tag
            if tag == "-text":
                parts.append(child.text(strip=False) or "")
            elif tag == "br":
                parts.append(" ")
            elif tag in SKIP_SPEECH_TAGS:
                pass
            elif tag == "a":
                epub_type = str(_attr(child, "epub:type")).lower()
                classes = {c.lower() for c in str(_attr(child, "class")).split()}
                href = str(_attr(child, "href"))
                if not ("noteref" in epub_type or "epub-noteref" in classes or href.startswith("#R_")):
                    walk(child)
            else:
                walk(child)
            child = nxt

    walk(element)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


@router.get("/api/ffmpeg/status")
async def get_ffmpeg_status():
    global ffmpeg_status
    if not ffmpeg_status.get("is_installed"):
        installer = FFMPEGInstaller()
        if installer.check_installed():
            ffmpeg_status["is_installed"] = True
    return ffmpeg_status


@router.post("/api/ffmpeg/install")
async def install_ffmpeg(background_tasks: BackgroundTasks):
    global ffmpeg_status, ffmpeg_installer

    if ffmpeg_status["is_downloading"]:
        return JSONResponse({"error": "Download already in progress"}, status_code=409)

    if ffmpeg_status["is_installed"]:
        return {"status": "already_installed"}

    def download_task():
        global ffmpeg_status, ffmpeg_installer
        ffmpeg_status["is_downloading"] = True
        ffmpeg_status["progress"] = 0
        ffmpeg_status["total"] = 0
        ffmpeg_status["error"] = None
        ffmpeg_status["message"] = "Starting download..."

        def progress_callback(current, total, message):
            ffmpeg_status["progress"] = current
            ffmpeg_status["total"] = total
            ffmpeg_status["message"] = message

        ffmpeg_installer = FFMPEGInstaller(progress_callback)
        success, error = ffmpeg_installer.install()

        if success:
            ffmpeg_status["is_installed"] = True
            ffmpeg_status["is_downloading"] = False
            ffmpeg_status["message"] = "Installation complete"
        else:
            ffmpeg_status["error"] = error
            ffmpeg_status["is_downloading"] = False

        ffmpeg_installer = None

    background_tasks.add_task(download_task)
    return {"status": "started"}


@router.post("/api/ffmpeg/cancel")
async def cancel_ffmpeg_download():
    global ffmpeg_installer
    if ffmpeg_installer:
        ffmpeg_installer.cancel()
        return {"status": "cancelled"}
    return {"status": "not_running"}


@router.post("/api/export/audio")
async def export_audio(request: ExportRequest, background_tasks: BackgroundTasks):
    global export_status
    if export_status["is_exporting"]:
        return JSONResponse({"error": "Export already in progress"}, status_code=409)
        
    start_tts_id = request.start_tts_id
    end_tts_id = request.end_tts_id

    import app.state as state_module
    if state_module.kokoro is None:
        raise HTTPException(status_code=503, detail="TTS Engine not initialized.")

    resolved_ffmpeg_path = None
    if request.format == "mp3":
        resolved_ffmpeg_path = get_ffmpeg_path()
        if not resolved_ffmpeg_path:
            ffmpeg_status["is_installed"] = False
            raise HTTPException(status_code=503, detail="FFMPEG not installed.")
        else:
            ffmpeg_status["is_installed"] = True

    def export_task():
        global export_status
        export_status = {
            "is_exporting": True, "progress": 0, "total": 0,
            "error": None, "output_file": None,
        }

        try:
            # 1. Load File (Supporting Legacy & New Paths)
            content_file_new = content_dir / request.doc_id / f"{request.doc_id}.json"
            content_file_legacy = content_dir / f"{request.doc_id}.json"
            
            if content_file_new.exists():
                content_file = content_file_new
            elif content_file_legacy.exists():
                content_file = content_file_legacy
            else:
                export_status["error"] = "Document not found"
                export_status["is_exporting"] = False
                return

            with open(content_file, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            with open(library_file, "r", encoding="utf-8") as f:
                library = json.load(f)

            doc_item = next((item for item in library if item.get("id") == request.doc_id), None)
            
            # 🌟 Extract the TOC Map for Header Rescues
            toc_map = doc_data.get("toc_map", [])

            # 2. Slice Pages based on UI Selection
            pages_list = doc_data.get("pages", [])
            s_page = request.start_page if request.start_page is not None else 0
            e_page = request.end_page if request.end_page is not None else len(pages_list)
            
            # 🌟 SURGICAL FIX: Prevent empty slice crash when TOC chapters share the same page
            if s_page >= e_page:
                e_page = s_page + 1
                
            target_pages = pages_list[s_page:e_page]

            # 3. HTML Parsing & Structural Chunking
            elements_to_process = []
            
            # Check if start_tts_id corresponds to the first TOC entry on s_page
            is_first_toc_on_page = True
            if start_tts_id:
                for idx, t in enumerate(toc_map):
                    t_target = t.get("target_tts_id")
                    t_anchor = (t.get("anchor_id") or "").split("#")[-1]
                    if t_target == start_tts_id or t_anchor == start_tts_id or t.get("id") == start_tts_id:
                        if idx > 0 and toc_map[idx - 1].get("page_index") == s_page:
                            is_first_toc_on_page = False
                        break
                        
            is_recording = not bool(start_tts_id) or is_first_toc_on_page
            
            for page_offset, page in enumerate(target_pages):
                curr_page_idx = s_page + page_offset
                is_first_page = (curr_page_idx == s_page)
                is_last_page = (curr_page_idx == e_page - 1)

                tree = HTMLParser(page)
                reached_end = False
                
                # Handle structured HTML format
                structured_elements = find_export_elements(tree)
                if structured_elements:
                    for idx_el, el in enumerate(structured_elements):
                        # 🌟 TOC ID MATCHING: Start and Stop recording dynamically
                        el_id = _attr(el, "id") or None
                        orig_id = _attr(el, "data-orig-id") or None
                            
                        # start_tts_id only activates on the first page of the range
                        if not is_recording and is_first_page:
                            if _element_matches_target_id(el, start_tts_id):
                                is_recording = True
                            elif el.tag in EXPORT_HEADING_TAGS:
                                # Rescue preceding heading when TOC target is first paragraph after heading
                                if (
                                    idx_el + 1 < len(structured_elements)
                                    and structured_elements[idx_el + 1].tag not in EXPORT_HEADING_TAGS
                                    and _element_matches_target_id(structured_elements[idx_el + 1], start_tts_id)
                                ):
                                    is_recording = True
                            
                        # end_tts_id only stops recording on the last page of the range
                        if is_recording and is_last_page and end_tts_id and _element_matches_target_id(el, end_tts_id):
                            reached_end = True
                            break
                            
                        if not is_recording:
                            continue
                            
                        b_type = "N"
                        clean_text = ""
                        
                        if el.tag in EXPORT_HEADING_TAGS:
                            b_type = el.tag.upper()
                            clean_text = extract_export_text(el)
                            
                            # 🌟 THE EXPORT HEADER RESCUE INTERCEPTOR 🌟
                            if not clean_text:
                                search_ids = [x for x in [el_id, orig_id] if x]
                                matched_toc = next((t for t in toc_map if (t.get("target_tts_id") and t.get("target_tts_id") in search_ids) or (t.get("id") and t.get("id") in search_ids)), None)
                                if matched_toc and matched_toc.get("title"):
                                    clean_text = matched_toc["title"]
                                    
                        elif el.tag == 'img':
                            b_type = "Img"
                            clean_text = "Image."
                        elif el.tag == 's':
                            b_type = "S"
                            clean_text = extract_export_text(el) or "..."
                        else:
                            # Standard block (p, blockquote, li, etc.)
                            # Detect standalone media blocks wrapped in <p>
                            has_img = bool(el.css_first("img, picture, svg"))
                            clean_text = extract_export_text(el)
                            if not clean_text and has_img:
                                b_type = "Img"
                                clean_text = "Image."
                            else:
                                b_type = "N"
                        
                        if clean_text or b_type in ["Img", "S"]:
                            elements_to_process.append({"text": clean_text, "b_type": b_type})

                # Failsafe: ensure recording is active for all subsequent pages after the start page
                if not is_recording:
                    is_recording = True

                if reached_end:
                    break

            if elements_to_process:
                elements_to_process[0]["is_first_element"] = True

            export_status["total"] = len(elements_to_process)
            print(f"[Export] Successfully extracted {len(elements_to_process)} elements to process.")
            
            if len(elements_to_process) == 0:
                export_status["error"] = "No extractable text found in selected range."
                export_status["is_exporting"] = False
                return


            # 🌟 SURGICAL FIX: Create the isolated "Audio files/[Book Name]" directory
            audio_dir = userdata_dir.parent / "Audio files"
            
            # Remove illegal Windows characters, but KEEP normal spaces
            safe_book_name = re.sub(r'[\\/*?:"<>|]', "", doc_item.get("fileName", "export")).strip()
            safe_label = re.sub(r'[\\/*?:"<>|]', "", request.file_label).strip()
            
            # 🌟 SURGICAL FIX: Cap filename length to prevent OS-level crashes on massive chapter titles
            if len(safe_label) > 100:
                safe_label = safe_label[:100].strip() + "..."
            
            book_audio_dir = audio_dir / safe_book_name
            book_audio_dir.mkdir(parents=True, exist_ok=True)

            temp_wav_path = book_audio_dir / f"temp_export_{request.doc_id}.wav"
            
           
            output_filename = f"{safe_label} ({request.voice}).{request.format}"
            output_path = book_audio_dir / output_filename
            
            wav_file = None
            generated_any = False

            # 5. Pipeline Execution Loop
            import concurrent.futures

            # 4. Core Audio Synthesis Manager (Reuses tts.py engine directly)
            class ExportSynthesisManager:
                """Manages audio generation for export elements by reusing tts.py:synthesize."""
                def __init__(self, req: ExportRequest):
                    self.req = req

                def process_element(self, el_data: dict) -> tuple[np.ndarray, int]:
                    if not export_status["is_exporting"]:
                        return np.array([], dtype=np.float32), 24000

                    try:
                        synth_req = SynthesisRequest(
                            text=el_data["text"],
                            voice=self.req.voice,
                            speed=float(self.req.speed),
                            rules=self.req.rules,
                            ignore_list=self.req.ignore_list,
                            pause_settings=self.req.pause_settings,
                            behavior_type=el_data["b_type"],
                            behavior_settings=dict(self.req.behavior_settings) if self.req.behavior_settings else None,
                        )
                        async def _get_audio():
                            response = await synthesize(synth_req)
                            chunks = [chunk async for chunk in response.body_iterator]
                            return b"".join(chunks)

                        audio_bytes = asyncio.run(_get_audio())
                        if not audio_bytes:
                            return np.array([], dtype=np.float32), 24000

                        samples, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
                        # 🌟 LEAD-IN NORMALIZATION: Normalize front pause on the very first element of the file
                        if el_data.get("is_first_element") and len(samples) > 0:
                            lead_in_frames = int(0.25 * sr)
                            active_indices = np.where(np.abs(samples) > 0.005)[0]
                            if len(active_indices) > 0:
                                first_active = active_indices[0]
                                if first_active > lead_in_frames:
                                    samples = samples[first_active - lead_in_frames:]
                        final_output = np.array(samples, copy=True)
                        del samples
                        return final_output, sr

                    except Exception as e:
                        print(f"[Export] Warning: Failed to process element '{el_data.get('text', '')[:20]}': {e}")
                        return np.array([], dtype=np.float32), 24000

            synthesis_manager = ExportSynthesisManager(request)

            # 🌟 DYNAMIC HARDWARE-AWARE BATCH PROCESSOR 🌟
            import gc
            import multiprocessing
            from ..config import settings_file
            
            # 1. Detect Hardware Mode
            engine_mode = "gpu"
            try:
                if settings_file.exists():
                    with open(settings_file, "r", encoding="utf-8") as sf_f:
                        user_settings = json.load(sf_f)
                        engine_mode = user_settings.get("engined_mode", "gpu")
            except Exception:
                pass
            
            # ---  HARDWARE VALIDATION LAYER ---
            # Prevents CPU choking if the system silently fell back to CPU mode
            if engine_mode == "gpu" and not has_onnxruntime_gpu():
                print("[Export] onnxruntime-gpu not found, skip GPU providers")
                engine_mode = "cpu"
            elif engine_mode == "gpu":
                try:
                    import onnxruntime as ort
                    available_providers = ort.get_available_providers()
                    gpu_providers = [
                        "CUDAExecutionProvider", 
                        "CoreMLExecutionProvider", 
                        "DmlExecutionProvider", 
                        "ROCMExecutionProvider",
                        "OpenVINOExecutionProvider"
                    ]
                    
                    # If no valid hardware provider is active in the engine, force CPU scaling
                    if not any(p in available_providers for p in gpu_providers):
                        print("[Export] Warning: GPU mode requested but no hardware provider found. Falling back to CPU scaling.")
                        engine_mode = "cpu"
                except Exception:
                    # Failsafe if ONNX isn't initialized yet
                    engine_mode = "cpu"

            total_cores = multiprocessing.cpu_count()
            reserved_cores = max(1, total_cores // 6)
            
            if engine_mode == "cpu":
                # 🌟 CPU MODE: BALANCE RAM & SPEED 🌟
                # Capped at 3 workers maximum to prevent memory saturation during fallback
                safe_workers = max(1, min(3, total_cores - reserved_cores))
                batch_size = safe_workers * 2
                print(f"[Export] CPU Mode:  Workers: {safe_workers} | Batch Size: {batch_size}")
                print("[Export WARNING] For maximum speed, please use GPU acceleration.")

            else:
                #  GPU MODE: THE VRAM it will eat ~1.2-2.5GB
                safe_workers = min(6, total_cores)
                batch_size = safe_workers * 4
                print(f"[Export] GPU Mode:  Workers: {safe_workers} | Batch Size: {batch_size}")
                print("\033[93m[Export WARNING] If export crashes or app closes, manually lock GPU core clock to minimum (via MSI Afterburner or nvidia-smi) to stabilize VRAM voltage.\033[0m")


            for batch_start in range(0, len(elements_to_process), batch_size):
                if not export_status["is_exporting"]:
                    export_status["error"] = "Export cancelled"
                    if wav_file: wav_file.close()
                    temp_wav_path.unlink(missing_ok=True)
                    return
                    
                batch = elements_to_process[batch_start:batch_start + batch_size]
                
                # 🌟 EXECUTION ROUTER: Prevent ThreadPool from duplicating memory in CPU mode
                if safe_workers == 1:
                    # Sequential map on the main thread
                    results = list(map(synthesis_manager.process_element, batch))
                else:
                    # GPU Multi-threading
                    with concurrent.futures.ThreadPoolExecutor(max_workers=safe_workers) as executor:
                        results = list(executor.map(synthesis_manager.process_element, batch))
                    
                # Write results sequentially to disk to preserve the audiobook timeline
                for i, (samples, sample_rate) in enumerate(results):
                    if not export_status["is_exporting"]:
                        break
                        
                    if wav_file is None and sample_rate > 0:
                        wav_file = sf.SoundFile(
                            str(temp_wav_path), mode='w', samplerate=sample_rate, 
                            channels=1, subtype='FLOAT'
                        )

                    if wav_file and len(samples) > 0:
                        wav_file.write(samples.flatten())
                        generated_any = True
                        
                    export_status["progress"] = batch_start + i + 1
                    
                # 🌟 AGGRESSIVE GARBAGE COLLECTION
                # Forcefully wipe the massive float32 audio arrays from RAM and VRAM after every batch
                del results
                del batch
                gc.collect()
            if wav_file:
                wav_file.close()

            if not generated_any:
                export_status["error"] = "No audio generated"
                export_status["is_exporting"] = False
                temp_wav_path.unlink(missing_ok=True)
                return

            if request.format == "mp3":
                export_status["progress"] = export_status["total"]
                try:
                    subprocess.run(
                        [str(resolved_ffmpeg_path), "-y", "-i", str(temp_wav_path), "-codec:a", "libmp3lame", "-b:a", "128k", str(output_path)],
                        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except Exception as e:
                    export_status["error"] = f"MP3 conversion failed: {str(e)}"
                    export_status["is_exporting"] = False
                    return
                finally:
                    temp_wav_path.unlink(missing_ok=True)
            else:
                shutil.move(str(temp_wav_path), str(output_path))

            export_status["error"] = None
            # Store the relative path so the UI knows which folder to open
            export_status["output_file"] = f"{safe_book_name}/{output_filename}"

        except Exception as e:
            export_status["error"] = str(e)
            if 'wav_file' in locals() and wav_file and not wav_file.closed:
                wav_file.close()
            if 'temp_wav_path' in locals() and temp_wav_path.exists():
                temp_wav_path.unlink(missing_ok=True)
                
        finally:
            export_status["is_exporting"] = False
            # Premature VRAM flush removed to prevent breaking the queue.

    background_tasks.add_task(export_task)
    return {"status": "started"}

@router.post("/api/export/flush-vram")
async def flush_export_vram(background_tasks: BackgroundTasks):
    def flush_task():
        print("[Export] Queue complete. Flushing RAM/VRAM and restoring idle state...")
        try:
            import gc
            import app.state as state_module
            
            # 🌟 DEEP C++ DESTRUCTION: Force OS to reclaim memory immediately
            for engine_attr in ["kokoro", "kokoro_export"]:
                engine = getattr(state_module, engine_attr, None)
                if engine is not None:
                    if hasattr(engine, "sess"):
                        del engine.sess  # Nuke the ONNX C++ InferenceSession directly
                    setattr(state_module, engine_attr, None)
                
            # Run GC multiple times to clear deep generational buffers
            gc.collect()
            gc.collect()
            
            # Reboot the engine smoothly in the background
            from .system import load_engine_logic
            load_engine_logic()
        except Exception as e:
            print(f"[Export] Memory Flush Warning: {e}")

    background_tasks.add_task(flush_task)
    return {"status": "flushing"}

@router.get("/api/export/status")
async def get_export_status():
    global export_status
    return export_status

@router.post("/api/export/cancel")
async def cancel_export():
    global export_status
    if export_status["is_exporting"]:
        export_status["is_exporting"] = False
        return {"status": "cancelled"}
    return {"status": "not_running"}

# Note: For nested downloads via URL, we use a catch-all path parameter.
@router.get("/api/export/download/{file_path:path}")
async def download_export(file_path: str):
    from fastapi.responses import FileResponse
    from ..config import userdata_dir
    target_path = userdata_dir.parent / "Audio files" / file_path
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "audio/mpeg" if file_path.endswith(".mp3") else "audio/wav"
    return FileResponse(target_path, media_type=media_type, filename=target_path.name)

from fastapi import Request

@router.post("/api/export/open-location")
async def open_file_location(req: Request = None):
    import os
    import platform
    import subprocess
    from ..config import userdata_dir
    
    try:
        # Self-Service: Always open the root Audio files directory
        audio_dir = userdata_dir.parent / "Audio files"
        audio_dir.mkdir(parents=True, exist_ok=True)
        
        system = platform.system()
        folder_str = str(audio_dir.resolve()) # .resolve() is safer for Windows os.startfile

        if system == "Windows":
            os.startfile(folder_str)
        elif system == "Darwin":
            subprocess.Popen(["open", folder_str])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", folder_str])
        else:
            raise HTTPException(status_code=501, detail="Platform not supported")

        return {"status": "opened", "folder": folder_str}

    except Exception as e:
        # Bulletproof Fallback: Open userdata if Audio files fails
        try:
            fallback_dir = str(userdata_dir.absolute())
            if system == "Windows":
                os.startfile(fallback_dir)
            elif system == "Darwin":
                subprocess.Popen(["open", fallback_dir])
            elif system == "Linux":
                subprocess.Popen(["xdg-open", fallback_dir])
            return {"status": "opened_fallback"}
        except Exception:
            raise HTTPException(status_code=500, detail=str(e))