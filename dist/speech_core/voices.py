"""The voice drawer's catalog: a model's voices grouped by language."""

from .tts_helpers import get_language_from_voice

LANGUAGE_LABELS = {
    "en-us": "English (US)", "en-gb": "English (UK)", "fr-fr": "French", "es": "Spanish",
    "cmn": "Chinese (Mandarin)", "it": "Italian", "pt-br": "Portuguese (Brazil)", "ja": "Japanese",
}


def voice_catalog(voices, hidden_languages=()):
    """{language: {"label", "voices": [{"id", "name"}]}}, names sorted per language."""
    categories = {}
    for voice in voices:
        voice_id = voice if isinstance(voice, str) else voice.get("id")
        language = get_language_from_voice(voice_id)
        if language in hidden_languages:
            continue
        parts = voice_id.split("_")
        name = parts[1].title() if len(parts) > 1 else voice_id
        group = categories.setdefault(language, {"label": LANGUAGE_LABELS.get(language, "Other"), "voices": []})
        group["voices"].append({"id": voice_id, "name": name})
    for group in categories.values():
        group["voices"].sort(key=lambda v: v["name"])
    return categories
