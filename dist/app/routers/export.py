from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from ..state import export_status, ffmpeg_status, kokoro
from ..config import content_dir, library_file, userdata_dir, base_dir
from ..models import ExportRequest
from ..utils import get_language_from_voice
import json
import re
import numpy as np
import os
import platform
import subprocess
import shutil
import soundfile as sf
import sys
from pathlib import Path

# Fix paths for logic imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

try:
    from logic.dependency_manager import FFMPEGInstaller, get_ffmpeg_path
    from logic.smart_content_detector import filter_text_for_tts
    from logic.text_normalizer import apply_custom_pronunciations
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from dependency_manager import FFMPEGInstaller, get_ffmpeg_path
    from smart_content_detector import filter_text_for_tts
    from text_normalizer import apply_custom_pronunciations

router = APIRouter()
ffmpeg_installer = None


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

    import app.state as state_module
    if state_module.kokoro is None:
        raise HTTPException(status_code=503, detail="TTS Engine not initialized.")

    resolved_ffmpeg_path = None
    if request.format == "mp3":
        resolved_ffmpeg_path = shutil.which("ffmpeg")
        if not resolved_ffmpeg_path:
            local_exe = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
            local_ffmpeg = base_dir / "bin" / local_exe
            if local_ffmpeg.exists():
                resolved_ffmpeg_path = str(local_ffmpeg)
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
            if not doc_item:
                export_status["error"] = "Document metadata not found"
                export_status["is_exporting"] = False
                return

            # 2. Slice Pages based on UI Selection
            pages_list = doc_data.get("pages", [])
            s_page = request.start_page if request.start_page is not None else 0
            e_page = request.end_page if request.end_page is not None else len(pages_list)
            
            # 🌟 SURGICAL FIX: Prevent empty slice crash when TOC chapters share the same page
            if s_page >= e_page:
                e_page = s_page + 1
                
            target_pages = pages_list[s_page:e_page]

            # 3. HTML Parsing & Structural Chunking
            from bs4 import BeautifulSoup
            elements_to_process = []
            
            for page in target_pages:
                soup = BeautifulSoup(page, 'html.parser')
                
                # Handle new structured HTML format
                # Handle new structured HTML format
                structured_elements = soup.find_all(['n', 's', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                if structured_elements:
                    for el in structured_elements:
                        b_type = "N"
                        clean_text = ""
                        
                        # 🌟 FIX: Safely route H tags without double-processing <n> children
                        if el.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                            if el.find('n'):
                                continue # Skip, let the child <n> tag handle it
                            b_type = el.name.upper()
                            clean_text = el.get_text(strip=True)
                        elif el.name == 'img':
                            if 'epub-image' in el.get('class', []):
                                b_type = "Img"
                                clean_text = "Image."
                            else:
                                continue
                        elif el.name == 's':
                            b_type = "S"
                            clean_text = el.get_text(strip=True) or "..."
                        elif el.name == 'n':
                            h_parent = el.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                            if h_parent:
                                b_type = h_parent.name.upper()
                            raw_text = el.get_text(strip=True)
                            clean_text = re.sub(r'<[^>]+>', '', raw_text).strip()
                        
                        if clean_text or b_type in ["Img", "S"]:
                            elements_to_process.append({"text": clean_text, "b_type": b_type})
                else:
                    # Strict Raw Fallback in case of formatting failure
                    lines = [p.strip() for p in page.split("\n") if p.strip()]
                    for line in lines:
                        b_type = "N"
                        if line.startswith("<h") and re.search(r'<h([1-6])', line, re.IGNORECASE):
                            match = re.search(r'<h([1-6])', line, re.IGNORECASE)
                            b_type = f"H{match.group(1)}"
                        elif "<img" in line.lower():
                            b_type = "Img"
                            line = "Image."
                        elif "<s>" in line.lower() or "scene-break" in line.lower():
                            b_type = "S"
                            line = "..."
                        
                        clean_text = re.sub(r'<[^>]+>', '', line).strip()
                        if clean_text or b_type in ["Img", "S"]:
                            elements_to_process.append({"text": clean_text, "b_type": b_type})

            export_status["total"] = len(elements_to_process)
            print(f"[Export] Successfully extracted {len(elements_to_process)} elements to process.")
            
            if len(elements_to_process) == 0:
                export_status["error"] = "No extractable text found in selected range."
                export_status["is_exporting"] = False
                return

            # 4. Core Audio Engineering Import
            from .tts import synthesize_with_pauses, create_anti_skip_silence, graceful_chunk_for_tts, generate_locked_audio, sanitize_typography_for_engine
            from logic.text_normalizer import apply_custom_pronunciations, fix_special_formats
            from logic.language_switcher import smart_polyglot_split
            from logic.japanese_g2p import pure_japanese_to_romaji
            from logic.chinese_g2p import cleanse_chinese_text
            from logic.syllable import estimate_phonemes

            rules_data = [r.model_dump() for r in request.rules] if request.rules else []
            main_voice_lang = get_language_from_voice(request.voice)
            
            pause_settings = request.pause_settings or {}
            behavior_settings = request.behavior_settings or {"H": 2000, "Img": 3000, "S": 1000, "N": 500}

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
            
            # Format: "Chapter 1 - Chapter 3 (af_heart).mp3"
            output_filename = f"{safe_label} ({request.voice}).{request.format}"
            output_path = book_audio_dir / output_filename
            
            wav_file = None
            generated_any = False

            # 5. Pipeline Execution Loop
            from .tts import _safe_cores
            import concurrent.futures

            def process_export_element(el_data):
                if not export_status["is_exporting"]:
                    return np.array([], dtype=np.float32), 24000
                    
                b_type = el_data["b_type"]
                raw_text = el_data["text"]
                sample_rate = 24000
                
                try:
                    # Apply pronunciation fixes
                    try:
                        text_norm = fix_special_formats(raw_text, main_voice_lang)
                        text_norm = apply_custom_pronunciations(text_norm, rules_data, request.ignore_list, main_voice_lang)
                    except Exception:
                        text_norm = raw_text

                    # Apply Polyglot Routing
                    polyglot_segments = smart_polyglot_split(text_norm, request.voice, get_language_from_voice)

                    # ==========================================
                    # 🌟 SHIELD & STRUCTURAL EXTRACTOR
                    # ==========================================
                    structural_pause_ms = 0
                    pause_match = re.search(r"\[PAUSE_(\d+)\]\s*", raw_text)
                    if pause_match:
                        structural_pause_ms = int(pause_match.group(1))
                        raw_text = raw_text.replace(pause_match.group(0), "")

                    # Apply TTS typographic shielding
                    raw_text = sanitize_typography_for_engine(raw_text)

                    # ==========================================
                    # 🌟 GOLDEN RATIO BEHAVIOR ENGINE
                    # ==========================================
                    behavior_front_pause_ms = 0
                    behavior_end_pause_ms = 0
                    
                    # 🌟 FIX: Local copy to prevent dictionary poisoning in the loop
                    local_b_settings = behavior_settings.copy()
                    
                    if b_type.startswith("H"):
                        h_base = local_b_settings.get("H", 2000)
                        if h_base > 0:
                            if b_type == "H1": base_calc = h_base
                            elif b_type == "H2": base_calc = h_base / 2.0
                            elif b_type == "H3": base_calc = (h_base / 2.0) / 1.5
                            else: base_calc = ((h_base / 2.0) / 1.5) / 1.5
                            
                            behavior_front_pause_ms = min(int(base_calc), 10000) 
                            behavior_end_pause_ms = min(int(base_calc * 0.30), 10000)
                        local_b_settings["N"] = 0
                        
                    elif b_type in ["Img", "S"]:
                        behavior_end_pause_ms = int(local_b_settings.get(b_type, 1000))
                        local_b_settings["N"] = 0
                        
                    else:
                        behavior_end_pause_ms = int(local_b_settings.get("N", 500))

                    # 'newline' in pause_settings since it moved to behavior_settings['N']
                    has_pause_settings = any(val > 0 for k, val in pause_settings.items() if k != "newline")
                    punctuation_chars = [",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、"]
                    
                    # 🌟 FIX: Guarantee micro-pauses trigger the formatting block
                    has_punctuation = any(p in text_norm for p in punctuation_chars) or "<CAP_COM>" in text_norm

                    chunk_audios = []
                    
                    # Shield against non-narrative components
                    if not re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", text_norm):
                        total_pause = structural_pause_ms + behavior_end_pause_ms + behavior_front_pause_ms
                        if total_pause <= 0: total_pause = 100
                        samples = create_anti_skip_silence(total_pause, 24000)
                    else:
                        for seg in polyglot_segments:
                            seg_text = seg['text']
                            seg_voice = seg['voice']
                            detected_lang = seg['lang']
                            native_voice_lang = get_language_from_voice(seg_voice)
                            
                            if not seg_text.strip(): continue
                            
                            final_text = seg_text
                            final_engine_lang = native_voice_lang
                            
                            if detected_lang == 'ja' or native_voice_lang.startswith('ja'):
                                final_text = pure_japanese_to_romaji(final_text)
                                final_engine_lang = 'en-us'
                            elif detected_lang == 'cmn' or native_voice_lang.startswith('cmn') or native_voice_lang.startswith('zh'):
                                final_text = cleanse_chinese_text(final_text)
                                if main_voice_lang.startswith('en') and seg_voice == request.voice:
                                    final_engine_lang = 'en-us'
                                else:
                                    final_engine_lang = native_voice_lang
                            else:
                                if main_voice_lang.startswith('ja') or main_voice_lang.startswith('cmn'):
                                    final_engine_lang = 'en-us'
                                else:
                                    final_engine_lang = native_voice_lang

                            if not final_text.strip(): continue
                            
                            if has_pause_settings and has_punctuation:
                                seg_samples, sr = synthesize_with_pauses(
                                    text=final_text, voice=seg_voice, speed=float(request.speed), 
                                    lang=final_engine_lang, pause_settings=pause_settings, behavior_settings=local_b_settings
                                )
                                if seg_samples is not None and len(seg_samples.flatten()) > 0: 
                                    chunk_audios.append(seg_samples.flatten())
                                sample_rate = sr
                            else:
                                # 🌟 FIX: UNSHIELD EVERYTHING for lines without structural pauses!
                                final_text = final_text.replace('<NUM_COM>', ',').replace('<NUM_DOT>', '.').replace('<NUM_COL>', ':').replace('<BYP_COM>', ',').replace('<CAP_COM>', ',')
                                sub_chunks = graceful_chunk_for_tts(final_text)
                                full_len = estimate_phonemes(final_text)
                                for chunk_dict in sub_chunks:
                                    chunk_samples, sr = generate_locked_audio(
                                        state_module.kokoro, chunk_dict["text"], seg_voice, 
                                        float(request.speed), final_engine_lang, full_len
                                    )
                                    if chunk_samples is not None and len(chunk_samples.flatten()) > 0: 
                                        chunk_audios.append(chunk_samples.flatten())
                                    sample_rate = sr
                                    
                        samples = np.concatenate(chunk_audios) if chunk_audios else np.array([], dtype=np.float32)
                        
                        # 🌟 THE NULLIFIER (Match tts.py behavior exactly)
                        if b_type == "N":
                            stripped_text = text_norm.strip()
                            has_hard_punc_at_end = False
                            
                            if stripped_text:
                                # 🌟 FIX: The Quote Penetrator
                                stripped_tail = re.sub(r'[\'"”’」』\s]+$', '', stripped_text).strip()
                                if stripped_tail:
                                    last_c = stripped_tail[-1]
                                    if last_c in ["!", "?", "！", "？"]:
                                        has_hard_punc_at_end = True
                            
                            if has_hard_punc_at_end or (has_pause_settings and has_punctuation and text_norm.endswith("\n")):
                                behavior_end_pause_ms = 0

                        total_front_pause_ms = behavior_front_pause_ms
                        total_end_pause_ms = structural_pause_ms + behavior_end_pause_ms
                        
                        # 🌟 Apply Pre-Reading Delay (Front Pause)
                        if total_front_pause_ms > 0:
                            frames = int((total_front_pause_ms / 1000.0) * sample_rate)
                            front_pause_arr = np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)
                            samples = np.concatenate([front_pause_arr, samples]) if len(samples) > 0 else front_pause_arr

                        # 🌟 Apply Post-Reading Delay (End Pause)
                        if total_end_pause_ms > 0:
                            frames = int((total_end_pause_ms / 1000.0) * sample_rate)
                            end_pause_arr = np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)
                            samples = np.concatenate([samples, end_pause_arr]) if len(samples) > 0 else end_pause_arr
                                
                        if len(samples) == 0:
                            samples = create_anti_skip_silence(100, sample_rate)

                    return samples, sample_rate

                except Exception as e:
                    print(f"[Export] Warning: Failed to process chunk: {e}")
                    return np.array([], dtype=np.float32), 24000

    
            # ==========================================
            # 🌟 DYNAMIC HARDWARE-AWARE BATCH PROCESSOR 🌟
            # ==========================================
            import gc
            import multiprocessing
            from ..config import settings_file
            
            # 1. Detect Hardware Mode
            engine_mode = "gpu"
            try:
                if settings_file.exists():
                    with open(settings_file, "r", encoding="utf-8") as sf_f:
                        user_settings = json.load(sf_f)
                        engine_mode = user_settings.get("engine_mode", "gpu")
            except Exception:
                pass
            
            total_cores = multiprocessing.cpu_count()
            
            if engine_mode == "cpu":
                # CPU MODE: Maximize thread usage but reserve system cores
                # Formula: 6 cores -> reserve 2; 12 cores -> reserve 3
                reserved_cores = max(2, total_cores // 4)
                safe_workers = max(1, total_cores - reserved_cores)
                batch_size = safe_workers * 2
                print(f"[Export] CPU Mode: {total_cores} Cores | Reserved: {reserved_cores} | Workers: {safe_workers}")
            else:
                # 🌟 GPU MODE: THE DATA STARVATION FIX 🌟
                # A single Python thread starves the GPU (12% usage) because Python's GIL and regex 
                # processing takes longer than the actual GPU math. 
                # By scaling to 4-8 concurrent workers, Python prepares sentences in parallel and 
                # feeds the GPU instantly, driving usage to maximum without overflowing typical VRAM pools.
                safe_workers = total_cores * 2
                batch_size = safe_workers * 4
                print(f"[Export] GPU Mode: MAXIMUM OVERDRIVE. Workers: {safe_workers} | Batch Size: {batch_size}")
            
            for batch_start in range(0, len(elements_to_process), batch_size):
                if not export_status["is_exporting"]:
                    export_status["error"] = "Export cancelled"
                    if wav_file: wav_file.close()
                    temp_wav_path.unlink(missing_ok=True)
                    return
                    
                batch = elements_to_process[batch_start:batch_start + batch_size]
                
                # Execute batch concurrently. .map() guarantees the array returns in perfect sequential order!
                with concurrent.futures.ThreadPoolExecutor(max_workers=safe_workers) as executor:
                    results = list(executor.map(process_export_element, batch))
                    
                # Write results sequentially to disk to preserve the audiobook timeline
                for i, (samples, sample_rate) in enumerate(results):
                    if not export_status["is_exporting"]:
                        break
                        
                    if wav_file is None and sample_rate > 0:
                        wav_file = sf.SoundFile(
                            str(temp_wav_path), mode='w', samplerate=sample_rate, 
                            channels=1, subtype='PCM_16'
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
            export_status["is_exporting"] = False

        except Exception as e:
            export_status["error"] = str(e)
            export_status["is_exporting"] = False
            if 'wav_file' in locals() and wav_file and not wav_file.closed:
                wav_file.close()
            if 'temp_wav_path' in locals() and temp_wav_path.exists():
                temp_wav_path.unlink(missing_ok=True)

    background_tasks.add_task(export_task)
    return {"status": "started"}

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
            os.startfile(str(userdata_dir.absolute()))
            return {"status": "opened_fallback"}
        except Exception:
            raise HTTPException(status_code=500, detail=str(e))