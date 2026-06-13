from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from ..state import export_status, ffmpeg_status
from ..config import content_dir, library_file, userdata_dir, base_dir, settings_file
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

    # ==========================================
    # 1. READ ACTIVE ENGINE & SETTINGS
    # ==========================================
    active_engine = "kokoro"
    pause_settings = {}
    try:
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            active_engine = settings.get("active_engine", "kokoro")
            pause_settings = settings.get("pause_settings", {})
    except Exception:
        pass

    # ==========================================
    # 2. VALIDATE LOADED ENGINE
    # ==========================================
    if active_engine == "kokoro" and getattr(state_module, 'kokoro', None) is None:
        raise HTTPException(status_code=503, detail="Kokoro Engine not initialized. Please load it first.")
    elif active_engine == "f5" and getattr(state_module, 'f5_model', None) is None:
        raise HTTPException(status_code=503, detail="F5-TTS Engine not initialized. Please load it first.")
    elif active_engine == "fish" and getattr(state_module, 'fish_engine', None) is None:
        raise HTTPException(status_code=503, detail="Fish-TTS Engine not initialized. Please load it first.")
    elif getattr(state_module, 'kokoro', None) is None and getattr(state_module, 'f5_model', None) is None and getattr(state_module, 'fish_engine', None) is None:
        raise HTTPException(status_code=503, detail="No TTS Engine is initialized. Please setup an engine.")

    # Only enforce FFMPEG checks if they requested an MP3
    if request.format == "mp3":
        if not ffmpeg_status["is_installed"]:
            installer = FFMPEGInstaller()
            if not installer.check_installed():
                raise HTTPException(
                    status_code=503, detail="FFMPEG not installed. Please install it first."
                )
            else:
                ffmpeg_status["is_installed"] = True

    def export_task():
        global export_status
        export_status = {
            "is_exporting": True,
            "progress": 0,
            "total": 0,
            "error": None,
            "output_file": None,
        }

        try:
            content_file = content_dir / f"{request.doc_id}.json"
            if not content_file.exists():
                export_status["error"] = "Document not found"
                export_status["is_exporting"] = False
                return

            with open(content_file, "r") as f:
                doc_data = json.load(f)

            with open(library_file, "r") as f:
                library = json.load(f)

            doc_item = next((item for item in library if item.get("id") == request.doc_id), None)
            if not doc_item:
                export_status["error"] = "Document metadata not found"
                export_status["is_exporting"] = False
                return

            chunks = []
            for page in doc_data.get("pages", []):
                page_paragraphs = [p.strip() for p in page.split("\n") if p.strip()]
                for para in page_paragraphs:
                    # Smart chunking keeps VRAM usage low for F5 and Fish
                    if len(para) > 500:
                        sentences = re.split(r"(?<=[.!?])\s+", para)
                        chunks.extend([s.strip() for s in sentences if s.strip()])
                    else:
                        chunks.append(para)

            export_status["total"] = len(chunks)
            rules_data = [r.model_dump() for r in request.rules]

            temp_wav_path = userdata_dir / f"temp_export_{request.doc_id}.wav"
            safe_filename = re.sub(r"[^\w\s-]", "", doc_item.get("fileName", "export")).replace(" ", "_")
            output_filename = f"{safe_filename}_{request.voice}.{request.format}"
            output_path = userdata_dir / output_filename
            
            wav_file = None
            generated_any = False

            for i, chunk in enumerate(chunks):
                if not export_status["is_exporting"]:
                    export_status["error"] = "Export cancelled"
                    if wav_file:
                        wav_file.close()
                    temp_wav_path.unlink(missing_ok=True)
                    return

                try:
                    filtered_text = filter_text_for_tts(chunk)
                    if not filtered_text or not re.search(r"[a-zA-Z0-9]", filtered_text):
                        export_status["progress"] = i + 1
                        continue

                    processed_text = apply_custom_pronunciations(
                        filtered_text, rules_data, request.ignore_list
                    )

                    samples = None
                    sample_rate = 24000

                    # ==========================================
                    # 3. UNIFIED INFERENCE ROUTING
                    # ==========================================
                    if active_engine == "fish":
                        from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
                        
                        voice_folder = base_dir / "voices" / "fish" / request.voice
                        ref_audio_path = voice_folder / "ref.wav"
                        ref_text_path = voice_folder / "ref.txt"

                        references = []
                        if ref_audio_path.exists() and ref_text_path.exists():
                            with open(ref_text_path, "r", encoding="utf-8") as f:
                                ref_text = f.read().strip()
                            with open(ref_audio_path, "rb") as f:
                                ref_audio_bytes = f.read()
                            references.append(ServeReferenceAudio(audio=ref_audio_bytes, text=ref_text))

                        req = ServeTTSRequest(
                            text=processed_text,
                            references=references,
                            chunk_length=200,
                            format="wav",
                            normalize=True
                        )

                        audio_chunks = []
                        sr = 44100
                        for result in state_module.fish_engine.inference(req):
                            if result.code in ["segment", "final"] and isinstance(result.audio, tuple):
                                sr = result.audio[0]
                                audio_chunks.append(result.audio[1])
                            elif result.code == "error":
                                raise Exception(str(result.error))

            # --- UPDATE INSIDE export.py (export_audio -> active_engine == "fish") ---

                        if audio_chunks:
                            samples = np.concatenate(audio_chunks)
                            sample_rate = sr
                            
                            # ==========================================
                            # SURGICAL FIX: FFMPEG High-Quality Time Stretch (Lossless Pipe)
                            # ==========================================
                            target_speed = float(request.speed)
                            if target_speed != 1.0:
                                try:
                                    import subprocess
                                    ffmpeg_exe = get_ffmpeg_path()
                                    if ffmpeg_exe:
                                        input_bytes = samples.tobytes()
                                        
                                        cmd = [
                                            str(ffmpeg_exe),
                                            "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
                                            "-i", "pipe:0",
                                            "-filter:a", f"atempo={target_speed}",
                                            "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
                                            "pipe:1"
                                        ]
                                        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                                        output_bytes, _ = process.communicate(input=input_bytes)
                                        
                                        if process.returncode == 0 and output_bytes:
                                            samples = np.frombuffer(output_bytes, dtype=np.float32)
                                        else:
                                            print("[FISH-TTS WARNING] FFMPEG failed to process speed. Falling back to 1.0x.")
                                    else:
                                        print("[FISH-TTS WARNING] FFMPEG not found. Skipping speed adjustment.")
                                except Exception as e:
                                    print(f"[FISH-TTS WARNING] FFMPEG pipe error: {str(e)}. Skipping speed adjustment.")
                            
                            # Fish Smart Pauses
                            extra_silence_ms = 0
                            if processed_text.endswith('\n'):
                                extra_silence_ms = pause_settings.get("newline", 800)
                            elif processed_text.rstrip().endswith('.') or processed_text.rstrip().endswith('。'):
                                extra_silence_ms = pause_settings.get("period", 600)
                            elif processed_text.rstrip().endswith('?') or processed_text.rstrip().endswith('？'):
                                extra_silence_ms = pause_settings.get("question", 600)
                            elif processed_text.rstrip().endswith('!') or processed_text.rstrip().endswith('！'):
                                extra_silence_ms = pause_settings.get("exclamation", 600)

                            if extra_silence_ms > 0:
                                # Shrink the pause gap if reading faster
                                if target_speed != 1.0:
                                    extra_silence_ms = int(extra_silence_ms / target_speed)
                                    
                                silence_samples = int((extra_silence_ms / 1000.0) * sample_rate)
                                silence_array = np.zeros(silence_samples, dtype=np.float32)
                                samples = np.concatenate([samples, silence_array])
                        else:
                            samples = np.zeros(int(44100 * 0.1), dtype=np.float32)
                            sample_rate = 44100

                    elif active_engine == "f5":
                        voice_folder = base_dir / "voices" / "f5" / request.voice
                        ref_audio_path = voice_folder / "ref.wav"
                        ref_text_path = voice_folder / "ref.txt"

                        if not ref_audio_path.exists() or not ref_text_path.exists():
                            voice_folder = base_dir / "voices" / "f5" / "default"
                            ref_audio_path = voice_folder / "ref.wav"
                            ref_text_path = voice_folder / "ref.txt"

                        with open(ref_text_path, "r", encoding="utf-8") as f:
                            ref_text = f.read().strip()

                        result = state_module.f5_model.infer(
                            ref_file=str(ref_audio_path), 
                            ref_text=ref_text, 
                            gen_text=processed_text, 
                            speed=float(request.speed)
                        )

                        if isinstance(result, tuple):
                            if len(result) == 3:
                                samples, sample_rate, _ = result
                            elif len(result) == 2:
                                samples, sample_rate = result
                            else:
                                samples = result[0]
                                sample_rate = 24000
                        else:
                            samples = result
                            sample_rate = 24000

                        if not isinstance(samples, np.ndarray):
                            import torch
                            if isinstance(samples, torch.Tensor):
                                samples = samples.cpu().numpy()
                            else:
                                samples = np.array(samples)
                                
                        if samples.ndim > 1:
                            samples = samples.flatten()

                        samples = samples.astype(np.float32)

                        # Standard F5 Pause
                        silence = np.zeros(int(sample_rate * 0.3), dtype=np.float32)
                        samples = np.concatenate([samples, silence])

                    else:
                        # Kokoro
                        lang = get_language_from_voice(request.voice)
                        samples, sample_rate = state_module.kokoro.create(
                            processed_text,
                            voice=request.voice,
                            speed=float(request.speed),
                            lang=lang,
                        )
                        # Standard Kokoro Pause
                        silence = np.zeros(int(sample_rate * 0.3), dtype=np.float32)
                        samples = np.concatenate([samples, silence])

                    # ==========================================
                    # 4. STREAM TO DISK
                    # ==========================================
                    if wav_file is None:
                        wav_file = sf.SoundFile(
                            str(temp_wav_path), 
                            mode='w', 
                            samplerate=sample_rate, 
                            channels=1, 
                            subtype='PCM_16'
                        )

                    wav_file.write(samples.flatten())
                    generated_any = True

                except Exception as e:
                    print(f"Warning: Failed to process chunk {i}: {e}")

                export_status["progress"] = i + 1

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
                    ffmpeg_exe = get_ffmpeg_path()
                    subprocess.run(
                        [str(ffmpeg_exe), "-y", "-i", str(temp_wav_path), "-codec:a", "libmp3lame", "-b:a", "128k", str(output_path)],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
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
            export_status["output_file"] = output_filename
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
    return export_status


@router.post("/api/export/cancel")
async def cancel_export():
    global export_status
    if export_status["is_exporting"]:
        export_status["is_exporting"] = False
        return {"status": "cancelled"}
    return {"status": "not_running"}


@router.get("/api/export/download/{filename}")
async def download_export(filename: str):
    file_path = userdata_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.post("/api/export/open-location/{filename}")
async def open_file_location(filename: str):
    try:
        file_path = userdata_dir / filename
        abs_file_path = file_path.absolute()

        if not abs_file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        folder_path = abs_file_path.parent
        if not folder_path.exists():
            folder_path.mkdir(parents=True, exist_ok=True)

        system = platform.system()
        folder_str = str(folder_path)

        if system == "Windows":
            os.startfile(folder_str)
        elif system == "Darwin":
            subprocess.Popen(["open", folder_str])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", folder_str])
        else:
            raise HTTPException(status_code=501, detail="Platform not supported")

        return {"status": "opened", "folder": folder_str}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))