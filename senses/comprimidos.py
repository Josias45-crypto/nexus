""".zip: lee cada archivo de adentro con su propio sentido, con límites contra zips bomba."""

import logging
import posixpath
import zipfile
from pathlib import Path

from config import settings
from core.chunker import Segment
from senses import Unsupported, extract_member, register

log = logging.getLogger("nexus.senses.zip")


def _skip(name: str) -> bool:
    base = posixpath.basename(name)
    return name.endswith("/") or name.startswith("__MACOSX/") or base.startswith(".") or not base


@register("zip", (".zip",), ("application/zip", "application/x-zip-compressed"))
def zip_file(path: Path, depth: int) -> list[Segment]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise Unsupported("zip dañado")
    with z:
        members = [i for i in z.infolist() if not _skip(i.filename)]
        if any(i.flag_bits & 0x1 for i in members):
            raise Unsupported("zip protegido con contraseña")
        if len(members) > settings.ARCHIVE_MAX_FILES:
            log.warning("Zip con %d archivos: se leen los primeros %d", len(members), settings.ARCHIVE_MAX_FILES)
            members = members[: settings.ARCHIVE_MAX_FILES]
        budget = settings.ARCHIVE_MAX_MB * 1024 * 1024
        segments, skipped = [], []
        for info in members:
            if info.file_size > budget:
                skipped.append(f"{info.filename} (supera NEXUS_ARCHIVE_MAX_MB)")
                continue
            budget -= info.file_size
            try:
                segments += extract_member(info.filename, z.read(info), "", depth + 1)
            except Unsupported as e:
                skipped.append(f"{info.filename} ({e})")
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as e:
                skipped.append(f"{info.filename} (no se pudo descomprimir: {e})")
    for s in skipped:
        log.info("Zip %s: omitido %s", path.name, s)
    if not segments and skipped:
        raise Unsupported(f"ningún archivo del zip se pudo leer; p. ej. {skipped[0]}")
    return segments
