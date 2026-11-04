"""Embeddings por OpenRouter (Fase 4; antes Ollama local), con caché por huella del texto.

La llamada vive en el cliente único (providers/openrouter.py: embed(), con lotes, reintentos,
política zdr + data_collection=deny y prefijos solo si el modelo los pide). Aquí: la caché en la
tabla embed_cache (sha256 del modelo, la dimensión, el tipo y el texto): un texto repetido (un
trozo reindexado, una pregunta que se repite) no se vuelve a pedir. Si OpenRouter no responde se
lanza ChatUnavailable: quien llama espera (el procesador deja el evento pendiente; la búsqueda
cae a solo palabras).
"""

import hashlib

import sqlite_vec

from config import settings
from core import clock
from core.db import connect
from providers.openrouter import get_client


def huella(texto: str, consulta: bool) -> str:
    base = f"{settings.EMBED_MODEL}\n{settings.EMBED_DIM}\n{'q' if consulta else 'd'}\n{texto}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _desde(blob: bytes) -> list[float]:
    import struct

    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class Embeddings:
    async def _embed(self, textos: list[str], consulta: bool) -> list[list[float]]:
        claves = [huella(t, consulta) for t in textos]
        with connect() as conn:
            guardados = {
                r["huella"]: _desde(r["vector"])
                for i in range(0, len(claves), 500)
                for r in conn.execute(
                    f"SELECT huella, vector FROM embed_cache WHERE huella IN ({','.join('?' * len(claves[i:i + 500]))})",
                    claves[i : i + 500],
                )
            }
        faltan = list(dict.fromkeys(c for c in claves if c not in guardados))
        if faltan:
            texto_de = dict(zip(claves, textos))
            nuevos = await get_client().embed([texto_de[c] for c in faltan], consulta)
            ahora = clock.now_utc().isoformat()
            with connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO embed_cache (huella, vector, created_at) VALUES (?, ?, ?)",
                    [(c, sqlite_vec.serialize_float32(v), ahora) for c, v in zip(faltan, nuevos)],
                )
            guardados.update(zip(faltan, nuevos))
        return [guardados[c] for c in claves]

    async def embed_documents(self, textos: list[str]) -> list[list[float]]:
        return await self._embed(textos, False)

    async def embed_query(self, texto: str) -> list[float]:
        return (await self._embed([texto], True))[0]


def get_embedder() -> Embeddings:
    return Embeddings()
