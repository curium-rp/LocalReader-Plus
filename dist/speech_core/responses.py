"""Speech as HTTP: raw PCM for live playback (and the bridge), WAV for downloads."""

import io

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from .pipeline import SpeechUnavailable


def _bypass_header(speech):
    # A unit that was only a missing language still answers with (silent) audio,
    # so playback and the completion gate move on.
    return {"X-Audio-Bypassed": ",".join(sorted(speech.bypassed))} if speech.bypassed else {}


def pcm_response(speech):
    data = np.asarray(speech.samples, dtype="<f4").ravel().tobytes()
    return Response(data, media_type="application/octet-stream", headers={
        "X-Audio-Format": "pcm_f32le",
        "X-Audio-Sample-Rate": str(speech.sample_rate),
        "X-Audio-Channels": "1",
        "X-Audio-Frames": str(len(data) // 4),
        **_bypass_header(speech),
    })


def wav_response(speech):
    import soundfile as sf
    buffer = io.BytesIO()
    sf.write(buffer, np.asarray(speech.samples).flatten(), speech.sample_rate, format="WAV", subtype="FLOAT")
    return Response(buffer.getvalue(), media_type="audio/wav", headers=_bypass_header(speech))


def speak(make_speech, audio_format="pcm_f32le", unavailable="TTS Engine not initialized."):
    """Run `make_speech()` and answer it in `audio_format`, with the HTTP errors
    both apps give: 503 without a model or language package, 500 otherwise."""
    try:
        speech = make_speech()
    except HTTPException:
        raise
    except SpeechUnavailable as e:
        raise HTTPException(status_code=503, detail=unavailable) from e
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return pcm_response(speech) if audio_format == "pcm_f32le" else wav_response(speech)
