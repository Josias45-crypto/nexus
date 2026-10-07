"""Escritura y borrado de trozos de memoria (tabla chunks + índices FTS y vectorial).

Todo lo de aquí es DERIVADO de los originales: se puede borrar y regenerar con /reindex.
Las funciones reciben la conexión para ir dentro de la transacción de quien llama.
"""

import json

import sqlite_vec


def insert_chunk(conn, event_id: str, position: int, content: str, vector, meta: dict | None = None) -> int:
    cid = conn.execute(
        "INSERT INTO chunks (event_id, position, content, meta) VALUES (?, ?, ?, ?)",
        (event_id, position, content, json.dumps(meta, ensure_ascii=False) if meta else None),
    ).lastrowid
    conn.execute("INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (cid, content))
    conn.execute(
        "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
        (cid, sqlite_vec.serialize_float32(vector)),
    )
    return cid


def delete_chunks(conn, event_id: str, only_summary: bool = False) -> int:
    """Borra los trozos de un evento de las tres tablas. only_summary: solo el resumen (-1)."""
    where = "event_id = ? AND position = -1" if only_summary else "event_id = ?"
    rows = conn.execute(f"SELECT id, content FROM chunks WHERE {where}", (event_id,)).fetchall()
    for r in rows:
        # FTS5 con contenido externo: el borrado necesita el texto original
        conn.execute(
            "INSERT INTO chunks_fts (chunks_fts, rowid, content) VALUES ('delete', ?, ?)",
            (r["id"], r["content"]),
        )
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (r["id"],))
    conn.execute(f"DELETE FROM chunks WHERE {where}", (event_id,))
    return len(rows)


def parse_meta(raw: str | None) -> dict:
    return json.loads(raw) if raw else {}
