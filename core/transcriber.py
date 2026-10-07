import logging
import re
import threading

from config import settings
from core.chunker import Segment

log = logging.getLogger("nexus.transcriber")

_model = None
_lock = threading.Lock()  # el modelo procesa un audio a la vez

WINDOW_CHARS = 600  # ventanas de ~600 caracteres, cortadas entre frases
# Frases que Whisper inventa en silencio o ruido (vienen de subtítulos de video)
HALLUCINATIONS = re.compile(
    r"subt[ií]tulos (realizados )?por la comunidad de amara\.org|amara\.org|"
    r"gracias por ver el v[ií]deo|suscr[ií]bete( al canal)?|"
    r"subt[ií]tulos por la comunidad|¡?gracias por ver!?$",
    re.I,
)
NO_SPEECH_PROB = 0.6


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


def _is_hallucination(seg) -> bool:
    text = seg.text.strip()
    if not text:
        return True
    if HALLUCINATIONS.search(text):
        return True
    # Whisper "seguro de que no hay voz" y con baja confianza: lo descartamos
    return seg.no_speech_prob > NO_SPEECH_PROB and seg.avg_logprob < -1.0


def transcribe_segments(path: str) -> list[Segment]:
    """Transcripción en ventanas de ~600 caracteres con su marca de tiempo (segundos)."""
    with _lock:
        model = _get_model()
        segments, info = model.transcribe(
            path,
            language=settings.WHISPER_LANG or None,
            vad_filter=True,  # ignora silencios
        )
        windows: list[Segment] = []
        text, start, end, dropped = "", None, 0.0, 0
        for seg in segments:
            if _is_hallucination(seg):
                dropped += 1
                continue
            if start is None:
                start = seg.start
            text = f"{text} {seg.text.strip()}".strip()
            end = seg.end
            if len(text) >= WINDOW_CHARS:
                windows.append(Segment(text, {"inicio": round(start, 1), "fin": round(end, 1)}))
                text, start = "", None
        if text:
            windows.append(Segment(text, {"inicio": round(start or 0, 1), "fin": round(end, 1)}))
    log.info(
        "Audio transcrito: %.0f s, idioma %s, %d ventana(s), %d segmento(s) descartado(s)",
        info.duration, info.language, len(windows), dropped,
    )
    return windows


def transcribe(path: str) -> str:
    return "\n\n".join(s.text for s in transcribe_segments(path))
