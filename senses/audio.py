"""Audio y video: transcripción local con faster-whisper (del video se usa solo el audio)."""

from pathlib import Path

from core.chunker import Segment
from senses import AUDIO_EXT, VIDEO_EXT, Unsupported, register


@register("audio", tuple(AUDIO_EXT), ("audio/",))
def audio(path: Path, depth: int) -> list[Segment]:
    from core.transcriber import transcribe_segments

    return transcribe_segments(str(path))


@register("video", tuple(VIDEO_EXT), ("video/",))
def video(path: Path, depth: int) -> list[Segment]:
    import av

    try:
        with av.open(str(path)) as container:
            has_audio = bool(container.streams.audio)
    except av.error.FFmpegError as e:
        raise Unsupported(f"video dañado o en un formato no soportado ({e})")
    if not has_audio:
        return []  # sin pista de audio: queda como "sin texto"
    return audio(path, depth)
