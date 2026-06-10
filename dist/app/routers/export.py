from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from ..state import export_status, ffmpeg_status, kokoro
from ..config import content_dir, library_file, userdata_dir
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
                    if len(para) > 500:
                        sentences = re.split(r"(?<=[.!?])\s+", para)
                        chunks.extend([s.strip() for s in sentences if s.strip()])
                    else:
                        chunks.append(para)

            export_status["total"] = len(chunks)
            rules_data = [r.model_dump() for r in request.rules]

            # ALWAYS stream to a temporary WAV first to preserve memory
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

                    lang = get_language_from_voice(request.voice)

                    samples, sample_rate = state_module.kokoro.create(
                        processed_text,
                        voice=request.voice,
                        speed=float(request.speed),
                        lang=lang,
                    )

                    if wav_file is None:
                        wav_file = sf.SoundFile(
                            str(temp_wav_path), 
                            mode='w', 
                            samplerate=sample_rate, 
                            channels=1, 
                            subtype='PCM_16'
                        )

                    wav_file.write(samples.flatten())
                    silence = np.zeros(int(sample_rate * 0.3), dtype=np.float32)
                    wav_file.write(silence)
                    
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

            # Process the format at the very end
            if request.format == "mp3":
                # Temporarily max out progress so the UI stays clean during conversion
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
                # If WAV, just rename the temp file to the final destination
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
    
    # Handle mimetypes dynamically
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