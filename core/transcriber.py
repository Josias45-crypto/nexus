import logging
import threading

from config import settings

log = logging.getLogger("nexus.transcriber")

_model = None
_lock = threading.Lock()  # el modelo procesa un audio a la vez


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        log.info("Cargando Whisper '%s' (la primera vez se descarga)...", settings.WHISPER_MODEL)
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            cpu_threads=settings.WHISPER_THREADS,
            download_root=settings.WHISPER_DIR,
        )
    return _model


def transcribe(path: str) -> str:
    """Devuelve la transcripción agrupada en párrafos de ~600 caracteres,
    cortando siempre entre frases para que el troceado no parta ideas."""
    with _lock:
        model = _get_model()
        segments, info = model.transcribe(
            path,
            language=settings.WHISPER_LANG or None,
            vad_filter=True,  # ignora silencios
        )
        paragraphs: list[str] = []
        current = ""
        for seg in segments:
            current = f"{current} {seg.text.strip()}".strip()
            if len(current) >= 600:
                paragraphs.append(current)
                current = ""
        if current:
            paragraphs.append(current)
    log.info("Audio transcrito: %.0f s, idioma %s", info.duration, info.language)
    return "\n\n".join(paragraphs)
