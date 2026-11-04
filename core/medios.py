"""Ver y oír (Fase 3): fotos, PDF escaneados, audios y videos, por OpenRouter con zdr.

- Procesar una sola vez: cada medio se identifica por su huella (events.sha256, única). Se analiza
  al llegar y el análisis queda en la tabla medios (descripción, texto, datos, transcripción),
  ligado al original; los trozos de la memoria se arman desde ahí. Si el mismo archivo llega otra
  vez, inbox lo reconoce y no se llama a la IA; reindexar tampoco la llama. Solo rever() (cuando él
  lo pide) vuelve a mirar el original.
- Fotos y páginas escaneadas: modelo de visión (NEXUS_OPENROUTER_MODEL_VISION, temperatura 0) que
  responde un JSON validado: clase, descripción, texto legible, datos y lo ilegible. Un dato
  (monto, fecha, nombre...) solo vale si aparece en el texto leído.
- PDF: las páginas con texto las lee senses/ (sin IA); las escaneadas van al modelo de visión, hasta
  [medios].pdf_paginas_max; si hay más, Isabelle avisa el costo aproximado y pregunta.
- Audios: transcripción (NEXUS_OPENROUTER_MODEL_AUDIO) en tramos de 5 min, hasta
  [medios].audio_max_min; el resto queda guardado sin transcribir. Se descartan las frases que
  Whisper inventa en silencio; si no queda nada claro, el audio queda «dudoso» y no entra a la memoria.
- Videos: solo se guardan (original intacto y registro), no se analizan.
- comentar(): el comentario de Isabelle (modelo diario, 0,8), verificado por código: cifras y
  nombres deben estar en el análisis y no puede suponer; si no pasa, un respaldo fijo. Los datos
  importantes los arma el código («Leí 450 soles; ¿es correcto?»).
"""

import asyncio
import io
import json
import logging
import re
import unicodedata
from pathlib import Path

from core import clock, iniciativa, memory, persona
from core.chunker import Segment
from core.db import connect
from providers.embeddings import get_embedder
from providers.openrouter import get_client
from senses import Unsupported, documentos, extract

log = logging.getLogger("nexus.medios")

TIPOS = ("foto", "pdf", "audio", "video")
CLASES = ("foto", "documento", "boleta", "factura", "captura", "otro")
DATOS = ("monto", "fecha", "plazo", "nombre", "numero", "otro")
IMPORTANTES = ("monto", "fecha", "plazo")  # se piden confirmar
LADO_MAX = 1600  # px del lado mayor de la foto que viaja al modelo de visión
VISION_TOKENS = 1500
PAGINAS_A_LA_VEZ = 4
PAGINAS_EXTRA_MAX = 200
RATE = 16000  # el audio viaja como Opus mono de 16 kHz: pesa poco y Whisper no necesita más
TRAMO_S = 300  # tramos de 5 min (los proveedores cortan a los 60 s de proceso)
VENTANA = 600  # caracteres por trozo de transcripción, con su marca de tiempo
NO_SPEECH, LOGPROB, COMPRESION = 0.6, -1.0, 2.4
# Frases que Whisper inventa en silencio o ruido (vienen de subtítulos de video)
ALUCINACIONES = re.compile(
    r"subt[ií]tulos (realizados |hechos )?por|amara\.org|gracias por ver|suscr[ií]bete|dale (a )?like"
    r"|activa la campanita|nos vemos en el pr[oó]ximo (video|v[ií]deo)|www\.|\.com\b", re.I)
SOLO_RUIDO = re.compile(r"^[\s¡!.,¿?…-]*(gracias|muchas gracias|m[uú]sica|aplausos|risas|thank you|you|bye|adi[oó]s)?[\s¡!.,¿?…\]\[)(-]*$", re.I)
COMENTARIO_TOKENS = 250
COMENTARIO_INTENTOS = 3
COMENTARIO_LARGO = 420
COMENTARIO_FRASES = 3
TEMPERATURA = 0.8

VER = """Mira la imagen y responde SOLO un JSON, en español:
{"tipo": "foto|documento|boleta|factura|captura|otro",
 "descripcion": "1 o 2 frases: qué se ve (personas, animales, objetos, lugar). Solo lo que se ve.",
 "texto": "todo el texto legible, tal cual, línea por línea (vacío si no hay texto)",
 "datos": [{"tipo": "monto|fecha|plazo|nombre|numero|otro", "valor": "tal cual aparece en la imagen", "que": "qué es, en 2 a 5 palabras"}],
 "ilegible": "qué partes no se leen bien (vacío si todo se lee)"}
En "datos" van solo los datos clave (totales, fechas, vencimientos, nombres, números de documento), como mucho 8.
No inventes ni completes nada que no se vea. Si una cifra o palabra no se lee bien, ponla en "ilegible" y no en "datos"."""

COMENTAR = """[TU TAREA]
Él te acaba de enviar {que}. Coméntalo como una persona real por WhatsApp: de 1 a 3 frases, natural y
creativo, con tu personalidad (cariño medio). Usa SOLO lo que está en [LO QUE {verbo}]: no agregues objetos,
personas, lugares, nombres ni cifras que no estén ahí y no opines de lo que no se ve ni se oye.
PROHIBIDO suponer para qué es, qué hace, qué siente o qué piensa él: nada de «se ve que», «se nota que»,
«parece que», «seguro que», «debe estar», «para tu obra», «para tus proyectos». Si quieres saberlo, pregúntaselo.
Los nombres que aparecen en un documento son de otras personas: a él no lo llames así.
{preguntas} Sin listas ni markdown."""
PREGUNTA_LIBRE = "Puedes terminar con UNA pregunta natural si viene al caso (por ejemplo, cómo se llama o de dónde es); como mucho una."
SIN_PREGUNTA = "No hagas ninguna pregunta y no escribas cifras ni fechas: la confirmación de los datos la pongo yo debajo."


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


def config() -> dict:
    c = persona.get().get("medios")
    if not isinstance(c, dict):
        raise persona.PersonaError(f"{persona.ARCHIVO}: falta la sección [medios]")
    for k in ("audio_max_min", "pdf_paginas_max", "audio_como_mensaje_max"):
        if not isinstance(c.get(k), (int, float)) or c[k] <= 0:
            raise persona.PersonaError(f"{persona.ARCHIVO}: [medios].{k} debe ser un número positivo")
    return c


def frase(clave: str, **datos) -> str:
    return persona.frase(f"medios.{clave}", **datos)


# ---------- tabla medios ----------


def get(event_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM medios WHERE event_id = ?", (event_id,)).fetchone()
    if not r:
        return None
    m = dict(r)
    for k, vacio in (("datos", []), ("partes", None), ("notas", [])):
        m[k] = json.loads(m[k]) if m[k] else vacio
    return m


def _guardar(event_id: str, campos: dict) -> None:
    """Guarda el análisis. Al volver a mirar un original se conserva lo que el jefe dijo de él."""
    now = clock.now_utc().isoformat()
    previo = get(event_id)
    fila = {
        "event_id": event_id, "tipo": campos["tipo"], "estado": campos["estado"], "clase": campos.get("clase"),
        "descripcion": campos.get("descripcion"), "texto": campos.get("texto"),
        "datos": json.dumps(campos.get("datos") or [], ensure_ascii=False),
        "ilegible": campos.get("ilegible"),
        "partes": json.dumps(campos["partes"], ensure_ascii=False) if campos.get("partes") is not None else None,
        "paginas_total": campos.get("paginas_total"), "duracion_s": campos.get("duracion_s"),
        "transcrito_s": campos.get("transcrito_s"),
        "notas": json.dumps((previo or {}).get("notas") or [], ensure_ascii=False),
        "confirmado": 0, "modelo": campos.get("modelo"), "costo_usd": campos.get("costo_usd"),
        "created_at": (previo or {}).get("created_at") or now, "updated_at": now,
    }
    with connect() as conn:
        conn.execute(f"INSERT OR REPLACE INTO medios ({', '.join(fila)}) VALUES ({', '.join('?' * len(fila))})",
                     tuple(fila.values()))


def ultimo(tipo: str | None = None) -> dict | None:
    """El último medio que llegó (de ese tipo, si se indica), con su archivo."""
    where, args = ("WHERE m.tipo = ?", (tipo,)) if tipo else ("", ())
    with connect() as conn:
        r = conn.execute(f"SELECT m.event_id FROM medios m JOIN events e ON e.id = m.event_id {where}"
                         " ORDER BY e.created_at DESC LIMIT 1", args).fetchone()
    return get(r["event_id"]) if r else None


# ---------- fotos ----------


def _jpeg(path: Path) -> bytes:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            img.seek(0)  # GIF o TIFF de varias páginas: la primera
            img = ImageOps.exif_transpose(img).convert("RGB")
    except UnidentifiedImageError:
        raise Unsupported(f"imagen dañada o en un formato no soportado ({path.suffix.lower()})")
    except Image.DecompressionBombError:
        raise Unsupported("imagen demasiado grande (más de 178 millones de píxeles)")
    img.thumbnail((LADO_MAX, LADO_MAX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _json(texto: str):
    texto = (texto or "").strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ValueError("no hay JSON")
    return json.loads(texto[inicio : fin + 1])


def _compacto(t: str) -> str:
    return re.sub(r"\s+", "", (t or "").lower())


def _en_texto(valor: str, texto: str) -> bool:
    """Un dato vale solo si aparece en el texto leído (sin contar espacios; 465,50 = 465.50)."""
    v, t = _compacto(valor), _compacto(texto)
    return bool(v) and (v in t or v.replace(",", ".") in t.replace(",", ".") or plano(valor) in plano(texto))


def _str(v) -> str:
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def validar(d) -> dict:
    """Valida lo que devolvió el modelo de visión; lanza ValueError si no sirve."""
    if not isinstance(d, dict):
        raise ValueError("no es un objeto")
    texto = str(d.get("texto") or "").strip() if isinstance(d.get("texto"), str) else ""
    desc = _str(d.get("descripcion"))
    if not desc and not texto:
        raise ValueError("sin descripción ni texto")
    datos = []
    for x in d.get("datos") or []:
        if not isinstance(x, dict):
            continue
        valor = _str(x.get("valor"))
        if valor and _en_texto(valor, texto) and len(datos) < 8:
            datos.append({"tipo": x.get("tipo") if x.get("tipo") in DATOS else "otro", "valor": valor,
                          "que": _str(x.get("que"))[:60]})
    return {"clase": d.get("tipo") if d.get("tipo") in CLASES else "otro", "descripcion": desc,
            "texto": texto, "datos": datos, "ilegible": _str(d.get("ilegible"))[:300]}


async def _ver(jpeg: bytes) -> tuple[dict, dict]:
    """Una imagen al modelo de visión: (análisis validado, uso). Dos intentos si el JSON llega roto."""
    for intento in (1, 2):
        salida, info = await get_client().ver(VER, [jpeg], max_tokens=VISION_TOKENS)
        try:
            return validar(_json(salida)), info
        except (ValueError, TypeError) as exc:
            log.warning("Visión: salida inválida (%s), intento %d de 2", type(exc).__name__, intento)
    raise ValueError("el modelo de visión no devolvió un análisis válido")


async def _analizar_foto(path: Path) -> dict:
    a, info = await _ver(await asyncio.to_thread(_jpeg, path))
    log.info("Foto analizada: clase %s, %d caracteres de texto, %d dato(s)", a["clase"], len(a["texto"]), len(a["datos"]))
    return {"tipo": "foto", "estado": "analizado", **a, "modelo": info.get("modelo"), "costo_usd": info.get("costo_usd")}


# ---------- PDF escaneados ----------


async def _ver_paginas(path: Path, numeros: list[int]) -> tuple[dict[str, dict], float]:
    jpegs = await asyncio.to_thread(documentos.paginas_jpeg, path, numeros)
    sem = asyncio.Semaphore(PAGINAS_A_LA_VEZ)

    async def una(n: int):
        async with sem:
            return n, *(await _ver(jpegs[n]))

    hechas = await asyncio.gather(*(una(n) for n in numeros))
    return {str(n): a for n, a, _ in hechas}, sum((i.get("costo_usd") or 0) for _, _, i in hechas)


async def _analizar_pdf(path: Path) -> dict | None:
    """None si todas las páginas traen texto (no hace falta la IA)."""
    texts = await asyncio.to_thread(documentos.textos, path)
    esc = documentos.escaneadas(texts)
    if not esc:
        return None
    hacer = esc[: int(config()["pdf_paginas_max"])]
    vistas, costo = await _ver_paginas(path, hacer)
    primera = vistas[str(hacer[0])]
    log.info("PDF: %d página(s) escaneada(s), %d leída(s) con visión", len(esc), len(hacer))
    return {
        "tipo": "pdf", "estado": "analizado", "clase": primera["clase"], "descripcion": primera["descripcion"],
        "texto": "\n\n".join(a["texto"] for a in vistas.values() if a["texto"]),
        "datos": [d for a in vistas.values() for d in a["datos"]][:12],
        "ilegible": "; ".join(f"pág. {n}: {a['ilegible']}" for n, a in vistas.items() if a["ilegible"])[:300],
        "partes": {n: a["texto"] or a["descripcion"] for n, a in vistas.items()}, "paginas_total": len(esc),
        "modelo": get_client().vision_model, "costo_usd": costo,
    }


def paginas_pendientes(m: dict) -> int:
    return max(0, (m.get("paginas_total") or 0) - len(m.get("partes") or {}))


def costo_paginas(m: dict, n: int) -> float:
    """Costo aproximado de leer n páginas más, según lo que costaron las ya leídas."""
    leidas = len(m.get("partes") or {}) or 1
    return (m.get("costo_usd") or 0) / leidas * n


async def mas_paginas(event_id: str, path: Path) -> int:
    """Lee las páginas escaneadas que faltaban (ya con el «sí» del jefe). Devuelve cuántas leyó."""
    m = get(event_id)
    texts = await asyncio.to_thread(documentos.textos, path)
    faltan = [n for n in documentos.escaneadas(texts) if str(n) not in (m["partes"] or {})][:PAGINAS_EXTRA_MAX]
    if not faltan:
        return 0
    vistas, costo = await _ver_paginas(path, faltan)
    partes = {**m["partes"], **{n: a["texto"] or a["descripcion"] for n, a in vistas.items()}}
    with connect() as conn:
        conn.execute("UPDATE medios SET partes = ?, costo_usd = COALESCE(costo_usd, 0) + ?, updated_at = ? WHERE event_id = ?",
                     (json.dumps(partes, ensure_ascii=False), costo, clock.now_utc().isoformat(), event_id))
    return len(faltan)


# ---------- audios ----------


def _tramos_opus(path: Path, max_s: float) -> tuple[list[tuple[bytes, float]], float]:
    """El audio como Opus mono de 16 kHz en tramos de TRAMO_S, hasta max_s segundos.
    Devuelve ([(ogg, segundos)], duración total). Lanza Unsupported si no se puede leer."""
    import av

    try:
        entrada = av.open(str(path))
    except (av.error.FFmpegError, OSError) as e:
        raise Unsupported(f"audio dañado o en un formato no soportado ({type(e).__name__})")
    tramos: list[tuple[bytes, float]] = []
    salida = None
    total = en_tramo = 0
    tope, por_tramo = int(max_s * RATE), TRAMO_S * RATE

    def cerrar():
        nonlocal salida
        if salida:
            buf, oc, st = salida
            for p in st.encode(None):
                oc.mux(p)
            oc.close()
            tramos.append((buf.getvalue(), en_tramo / RATE))
            salida = None

    with entrada:
        if not entrada.streams.audio:
            raise Unsupported("el archivo no tiene audio")
        res = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        try:
            for frame in entrada.decode(audio=0):
                for f in res.resample(frame):
                    if total < tope:
                        if salida is None:
                            buf = io.BytesIO()
                            oc = av.open(buf, "w", format="ogg")
                            st = oc.add_stream("libopus", rate=RATE)
                            st.layout = "mono"
                            salida, en_tramo = (buf, oc, st), 0
                        f.pts = None
                        for p in salida[2].encode(f):
                            salida[1].mux(p)
                        en_tramo += f.samples
                        if en_tramo >= por_tramo:
                            cerrar()
                    total += f.samples
        except av.error.FFmpegError as e:
            if not total:
                raise Unsupported(f"audio dañado ({type(e).__name__})")
            log.warning("Audio cortado o dañado al final (%s): se usa lo leído", type(e).__name__)
        cerrar()
    return tramos, total / RATE


def alucinacion(seg: dict) -> bool:
    """Un segmento que Whisper inventó (silencio, ruido o frases de subtítulos)."""
    texto = str(seg.get("text") or "").strip()
    if not texto or ALUCINACIONES.search(texto) or SOLO_RUIDO.match(texto):
        return True
    ns, lp = seg.get("no_speech_prob"), seg.get("avg_logprob")
    if isinstance(ns, (int, float)) and isinstance(lp, (int, float)) and ns > NO_SPEECH and lp < LOGPROB:
        return True
    cr = seg.get("compression_ratio")
    return isinstance(cr, (int, float)) and cr > COMPRESION  # bucles: «gracias gracias gracias…»


def limpiar_transcripcion(segmentos: list[dict]) -> tuple[list[dict], bool]:
    """(segmentos que quedan, dudosa). Dudosa: no queda nada claro o la confianza es baja."""
    buenos = [s for s in segmentos if not alucinacion(s)]
    palabras = sum(len(plano(s["text"]).split()) for s in buenos)
    probs = [s["avg_logprob"] for s in buenos if isinstance(s.get("avg_logprob"), (int, float))]
    dudosa = palabras < 2 or (bool(probs) and sum(probs) / len(probs) < LOGPROB)
    return buenos, dudosa


def ventanas(segmentos: list[dict]) -> list[dict]:
    """Ventanas de ~600 caracteres con su marca de tiempo (para citar «min 01:23»)."""
    out, texto, inicio, fin = [], "", None, 0.0
    for s in segmentos:
        inicio = s["start"] if inicio is None else inicio
        texto, fin = f"{texto} {s['text'].strip()}".strip(), s["end"]
        if len(texto) >= VENTANA:
            out.append({"inicio": round(inicio, 1), "fin": round(fin, 1), "texto": texto})
            texto, inicio = "", None
    if texto:
        out.append({"inicio": round(inicio or 0, 1), "fin": round(fin, 1), "texto": texto})
    return out


async def _analizar_audio(path: Path) -> dict:
    max_s = float(config()["audio_max_min"]) * 60
    tramos, total = await asyncio.to_thread(_tramos_opus, path, max_s)
    segmentos, costo, desde = [], 0.0, 0.0
    for data, dur in tramos:
        r, info = await get_client().transcribir(data, "ogg")
        costo += info.get("costo_usd") or 0
        segs = r.get("segments") or [{"start": 0.0, "end": dur, "text": r.get("text") or ""}]
        for s in segs:
            segmentos.append({**s, "text": str(s.get("text") or ""), "start": desde + float(s.get("start") or 0),
                              "end": desde + float(s.get("end") or 0)})
        desde += dur
    buenos, dudosa = limpiar_transcripcion(segmentos)
    log.info("Audio: %.0f s (%.0f transcritos), %d tramo(s), %d segmento(s) descartado(s)%s",
             total, desde, len(tramos), len(segmentos) - len(buenos), ", dudoso" if dudosa else "")
    partes = [] if dudosa else ventanas(buenos)
    return {"tipo": "audio", "estado": "dudoso" if dudosa else "analizado",
            "texto": " ".join(p["texto"] for p in partes), "partes": partes,
            "duracion_s": round(total, 1), "transcrito_s": round(desde, 1),
            "modelo": get_client().audio_model, "costo_usd": costo}


# ---------- entrada del procesador ----------


def es_medio(row: dict) -> bool:
    return row["kind"] in ("image", "audio", "video") or (
        row["kind"] == "document" and Path(row["raw_path"]).suffix.lower() == ".pdf")


async def extraer(row: dict, forzar: bool = False) -> list[Segment]:
    """Segmentos de un evento. Fotos, audios, videos y PDF escaneados se analizan una sola vez
    (tabla medios); con forzar se vuelve a mirar el original. Lo demás va a senses/ sin IA."""
    path = Path(row["raw_path"])
    if es_medio(row):
        m = None if forzar else get(row["id"])
        if m is None:
            if row["kind"] == "image":
                campos = await _analizar_foto(path)
            elif row["kind"] == "audio":
                campos = await _analizar_audio(path)
            elif row["kind"] == "video":
                campos = {"tipo": "video", "estado": "guardado"}
            else:
                campos = await _analizar_pdf(path)
            if campos:
                _guardar(row["id"], campos)
                m = get(row["id"])
        if m:
            return await asyncio.to_thread(segmentos, m, path)
    return await asyncio.to_thread(extract, path, row.get("mime") or "")


def _que(m: dict) -> str:
    return {"foto": "esta foto", "pdf": "este documento", "audio": "este audio", "video": "este video"}[m["tipo"]]


def segmentos(m: dict, path: Path) -> list[Segment]:
    """Los trozos de la memoria salen del análisis guardado (no se vuelve a llamar a la IA)."""
    segs: list[Segment] = []
    if m["tipo"] == "foto":
        segs.append(Segment(f"Foto ({m['clase']}): {m['descripcion']}", {"descripcion": True}))
        if m["texto"]:
            segs.append(Segment(m["texto"], {"texto_foto": True}, markdown=False))
    elif m["tipo"] == "pdf":
        por_pagina = {s.meta["pagina"]: s for s in extract(path, "application/pdf")}
        for n, t in (m["partes"] or {}).items():
            por_pagina[int(n)] = Segment(t, {"pagina": int(n), "vision": True}, markdown=False)
        segs += [por_pagina[n] for n in sorted(por_pagina)]
    elif m["tipo"] == "audio" and m["estado"] == "analizado":
        segs += [Segment(p["texto"], {"inicio": p["inicio"], "fin": p["fin"]}) for p in m["partes"] or []]
    if m["datos"]:
        segs.append(Segment("Datos leídos: " + "; ".join(f"{d['que'] or d['tipo']}: {d['valor']}" for d in m["datos"])
                            + (" (el jefe los confirmó)" if m["confirmado"] else ""), {"datos": True}))
    for n in m["notas"]:
        segs.append(Segment(f"Lo que el jefe dijo sobre {_que(m)}: {n['texto']}", {"nota_del_jefe": True}))
    return [s for s in segs if s.text.strip()]


# ---------- lo que el jefe dice del medio ----------


async def anotar(ids: list[str], texto: str) -> None:
    """Su respuesta («se llama Max») queda ligada al medio: en la tabla y como trozo de la memoria."""
    embedder = get_embedder()
    for eid in ids:
        m = get(eid)
        if not m:
            continue
        notas = m["notas"] + [{"texto": texto, "fecha": clock.now_utc().isoformat()}]
        contenido = f"Lo que el jefe dijo sobre {_que(m)}: {texto}"
        vec = (await embedder.embed_documents([contenido]))[0]
        with connect() as conn:
            conn.execute("UPDATE medios SET notas = ?, updated_at = ? WHERE event_id = ?",
                         (json.dumps(notas, ensure_ascii=False), clock.now_utc().isoformat(), eid))
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM chunks WHERE event_id = ?", (eid,)).fetchone()[0]
            memory.insert_chunk(conn, eid, pos, contenido, vec, {"nota_del_jefe": True})


def confirmar(ids: list[str]) -> None:
    with connect() as conn:
        conn.executemany("UPDATE medios SET confirmado = 1, updated_at = ? WHERE event_id = ?",
                         [(clock.now_utc().isoformat(), eid) for eid in ids])


# ---------- comentario de Isabelle ----------


def importantes(ms: list[dict]) -> list[dict]:
    return [d for m in ms for d in m["datos"] if d["tipo"] in IMPORTANTES][:3]


def _visto(m: dict, n: int | None = None) -> str:
    cab = f"{['Foto', 'Documento'][m['tipo'] == 'pdf']}{f' {n}' if n else ''}"
    if m["tipo"] == "audio":
        return f"Audio de {round((m['duracion_s'] or 0) / 60, 1)} min. Transcripción: {(m['texto'] or '')[:1500]}"
    partes = [f"{cab} ({m['clase']}): {m['descripcion']}"]
    if m["texto"]:
        partes.append(f"Texto legible: {m['texto'][:1200]}")
    if m["datos"]:
        partes.append("Datos: " + "; ".join(f"{d['que'] or d['tipo']}: {d['valor']}" for d in m["datos"]))
    if m["ilegible"]:
        partes.append(f"No se lee bien: {m['ilegible']}")
    return "\n".join(partes)


def verificar(texto: str, ctx: str, max_preguntas: int, nombres: list[str] | None = None) -> str | None:
    """None si el comentario sirve; si no, el motivo. Toda cifra y nombre propio debe estar en el
    análisis; de 1 a 3 frases; sin listas, sin suposiciones, con el número de preguntas permitido y
    sin llamarlo a él con un nombre leído en el documento («¡Qué compra, Josué!»)."""
    t = " ".join(texto.split())
    if not t:
        return "vacío"
    if len(t) > COMENTARIO_LARGO or len(re.findall(r"[.!?…]+(?=\s|$)", t)) > COMENTARIO_FRASES or iniciativa.RE_LISTA.search(texto):
        return "largo o con lista"
    if t.count("?") > max_preguntas:
        return "preguntas"
    if iniciativa.RE_VIDA.search(t):
        return "vida propia"
    palabras = set(plano(ctx).split())
    for m in iniciativa.RE_ESPECULA.finditer(t):
        nuevas = [w for w in plano(m.group("resto")).split() if w not in iniciativa.VACIAS and w not in palabras]
        if nuevas or not m.group("resto").strip():
            return f"supone ({' '.join(plano(m.group(0)).split()[:6])})"
    cifras = set(re.findall(r"\d+", ctx))
    for n in re.findall(r"\d+", t):
        if n not in cifras:
            return f"cifra {n}"
    for w in plano(t).split():
        if w in iniciativa.NUMEROS and str(iniciativa.NUMEROS[w]) not in cifras and w not in palabras:
            return f"cifra {w}"
    for nombre in nombres or []:
        for w in re.findall(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+", nombre):
            if re.search(rf"(?:^|[,¡¿])\s*{w}\s*[,!?.]", t):
                return f"lo llama {w}"
    for m in iniciativa.RE_NOMBRE.finditer(t):
        antes = t[: m.start()].rstrip()
        if not antes or antes[-1] in ".!?¿¡:\"«":
            continue  # inicio de frase: no se distingue de un nombre
        w = plano(m.group(0))
        if w not in iniciativa.TRATO_PALABRAS and w not in iniciativa.PERMITIDOS and w not in palabras:
            return f"nombre {m.group(0)}"
    return None


def _respaldo(ms: list[dict]) -> str:
    m = ms[0]
    if m["tipo"] == "audio":
        return frase("respaldo_audio")
    desc = (m["descripcion"] or "").rstrip(".")
    return frase("respaldo_foto", que=desc[:1].lower() + desc[1:]) if desc else frase("respaldo_audio")


async def comentar(ms: list[dict], leyenda: str = "", con_datos: bool = True, preguntar: bool = True) -> tuple[str, dict | None]:
    """El comentario de Isabelle sobre uno o varios medios ya analizados (un álbum es una sola
    respuesta). Una llamada al modelo diario, verificada; si no pasa, hasta COMENTARIO_INTENTOS y luego
    un respaldo fijo.
    Debajo, por código: lo ilegible y los datos importantes para confirmar."""
    datos = importantes(ms) if con_datos else []
    preguntas = SIN_PREGUNTA if datos else PREGUNTA_LIBRE if preguntar else "No hagas ninguna pregunta: la pregunta la pongo yo debajo."
    oye = all(m["tipo"] == "audio" for m in ms)
    que = (f"{len(ms)} fotos juntas" if len(ms) > 1 else
           {"foto": "una foto", "pdf": "un documento escaneado", "audio": "un audio largo"}[ms[0]["tipo"]])
    visto = "\n\n".join(_visto(m, i if len(ms) > 1 else None) for i, m in enumerate(ms, 1))
    ctx = visto + ("\n" + leyenda if leyenda else "")
    sistema = (f"[QUIÉN ERES]\n{persona.sistema()}\n\n[LO QUE {'ESCUCHÉ' if oye else 'VI'}]\n{visto}"
               + (f"\n\n[LO QUE ÉL ESCRIBIÓ CON EL ARCHIVO]\n{leyenda}" if leyenda else "")
               + "\n\n" + COMENTAR.format(que=que, verbo="ESCUCHÉ" if oye else "VI",
                                          preguntas=preguntas))
    texto, uso, motivo = "", None, None
    for _ in range(COMENTARIO_INTENTOS):
        salida, uso = await get_client().chat_ex(
            [{"role": "system", "content": sistema}, {"role": "user", "content": "Coméntalo."}],
            temperature=TEMPERATURA, max_tokens=COMENTARIO_TOKENS)
        salida = " ".join((salida or "").split())
        nombres = [d["valor"] for m in ms for d in m["datos"] if d["tipo"] == "nombre"]
        motivo = verificar(salida, ctx, 1 if preguntar and not datos else 0, nombres)
        if not motivo:
            texto = salida
            break
        log.info("Comentario descartado: %s", motivo)
    if not texto:
        texto = _respaldo(ms)
    lineas = [texto]
    ilegible = "; ".join(m["ilegible"] for m in ms if m.get("ilegible"))
    if ilegible:
        lineas.append(frase("ilegible", que=ilegible.rstrip(".")))
    if datos:
        lineas.append(frase("cifras", cifras=", ".join(f"{d['valor']} ({d['que']})" if d["que"] else d["valor"] for d in datos)))
        lineas.append(frase("recordatorio"))
    return "\n\n".join(lineas), uso


def duplicado(m: dict) -> str:
    if m["tipo"] == "foto" and m.get("descripcion"):
        desc = m["descripcion"].rstrip(".")
        return frase("duplicado_foto", que=desc[:1].lower() + desc[1:])
    return frase(f"duplicado_{m['tipo']}" if m["tipo"] in ("audio", "pdf", "video") else "duplicado_pdf")
