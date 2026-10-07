import logging

from config import settings


def setup_logging() -> None:
    """Formato único con hora. Nunca se registran prompts, textos del usuario ni keys."""
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx registra cada petición en INFO (incluidas URLs de proveedores): solo avisos
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
