"""Pruebas. Las unitarias corren sin .env: modelo y dimensión de embeddings de prueba (los
vectores salen de proveedores simulados; nunca se llama a OpenRouter por esto)."""

import os

os.environ.setdefault("NEXUS_EMBED_MODEL", "prueba/embeddings")
os.environ.setdefault("NEXUS_EMBED_DIM", "8")
