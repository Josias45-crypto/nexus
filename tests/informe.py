#!/usr/bin/env python3
"""Informe rápido: qué puede y qué no puede hacer NEXUS hoy. Uso: python3 tests/informe.py"""
import json, re, subprocess, sys, time, urllib.parse, urllib.request, uuid
from datetime import datetime, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
RUN = uuid.uuid4().hex[:8]
filas = []


def call(path, data=None, timeout=180):
    req = urllib.request.Request(BASE + path, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        try:
            return json.loads(body)
        except ValueError:
            return body


def prueba(nombre, fn):
    t0 = time.time()
    try:
        estado, detalle = fn()
    except Exception as e:
        estado, detalle = "FALLA", f"{type(e).__name__}: {e}"
    filas.append((nombre, estado, f"{time.time() - t0:.1f} s", detalle))


def limpio(s):
    return re.sub(r"[\s\u202f\u00a0.,]", "", str(s))


def t_salud():
    h = call("/health")
    return "OK", f"modelo={h.get('llm_activo')}, nube={h.get('nube')}"


def t_guardar_y_preguntar():
    marca = f"Zorvak{RUN}"
    call("/inbox/text", {"text": f"El cliente {marca} compró 5 impresoras por 3200 soles y paga el viernes.", "source": "informe"})
    t0 = time.time()
    while time.time() - t0 < 180:
        if marca in json.dumps(call("/search?" + urllib.parse.urlencode({"q": marca})), ensure_ascii=False):
            break
        time.sleep(5)
    else:
        return "FALLA", "la nota no apareció en la búsqueda en 3 min"
    t1 = time.time()
    r = call("/ask", {"question": f"¿Cuánto pagó el cliente {marca}?"})
    resp = r.get("answer", "") if isinstance(r, dict) else str(r)
    ok = "3200" in limpio(resp)
    return ("OK" if ok else "FALLA"), f"respondió en {time.time() - t1:.1f} s: {resp[:80]!r}"


def t_no_se():
    r = call("/ask", {"question": f"¿Cuál es el código secreto del proyecto Quimera{RUN}?"})
    resp = r.get("answer", "") if isinstance(r, dict) else str(r)
    ok = "no tengo informaci" in resp.lower()
    return ("OK" if ok else "FALLA"), f"{resp[:80]!r}"


def t_worker():
    w = call("/worker")
    est = w.get("eventos_por_estado", {})
    malos = est.get("failed", 0)
    return ("OK" if not malos else "AVISO"), f"estados={est}"


def t_telegram():
    nombres = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    ok = "nexus-telegram" in nombres
    return ("OK" if ok else "FALLA"), "contenedor del bot " + ("encendido" if ok else "APAGADO (usa --profile channels)")


def t_salida():
    call("/outbox?channel=telegram")
    return "OK", "la cola de mensajes salientes responde"


CODIGO = r'''
import sqlite3, json
c = sqlite3.connect("file:/data/nexus.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
t = [r[0] for r in c.execute(
    "select name from sqlite_master where type='table' and (name like '%remind%'"
    " or name like '%record%' or name like '%pending%' or name like '%confirm%')")]
out = {"tablas": {}}
for name in t:
    total = c.execute(f"select count(*) from {name}").fetchone()[0]
    filas = [dict(r) for r in c.execute(f"select * from {name} order by rowid")]
    out["tablas"][name] = {"total": total, "filas": filas}
out["outbox_recordatorios"] = [dict(r) for r in c.execute(
    "select id, created_at, channel, ref_id, delivered_at from outbox where kind = 'reminder'"
    " order by id desc limit 5")]
print(json.dumps(out, default=str, ensure_ascii=False))
'''
GRACIA_S = 180  # el programador revisa cada 30 s; más de 3 min sin enviar es un fallo


def _fecha(v):
    h = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return h if h.tzinfo else h.replace(tzinfo=timezone.utc)


def _leer_tablas():
    p = subprocess.run(["docker", "exec", "-i", "nexus-core", "python", "-"], input=CODIGO,
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-150:])
    return json.loads(p.stdout)


def t_recordatorios():
    d = _leer_tablas()
    tablas = d["tablas"]
    if "reminders" not in tablas:
        return "FALLA", "no existe la tabla de recordatorios"
    ahora = datetime.now(timezone.utc)
    atrasados, resumen = [], []
    for nombre, info in tablas.items():
        estados = {}
        for f in info["filas"]:
            if "status" in f:
                estados[f["status"]] = estados.get(f["status"], 0) + 1
        resumen.append(f"{nombre}: {info['total']} fila(s)" + (f" {estados}" if estados else ""))
    for f in tablas["reminders"]["filas"]:
        if f.get("status") == "pending" and (ahora - _fecha(f["due_at"])).total_seconds() > GRACIA_S:
            atrasados.append(f["id"])
    sin_entregar = [m["ref_id"] for m in d["outbox_recordatorios"] if not m["delivered_at"]]
    detalle = " · ".join(resumen)
    if sin_entregar:
        detalle += f" · en el buzón sin entregar: {sin_entregar}"
    if atrasados:
        return "FALLA", f"vencidos SIN enviar: {atrasados} · " + detalle
    return "OK", detalle


def t_confirmaciones():
    d = _leer_tablas()
    filas = d["tablas"].get("pending_confirmations")
    if filas is None:
        return "FALLA", "no existe pending_confirmations: el 'sí' se pierde si el bot se reinicia"
    ahora = datetime.now(timezone.utc)
    esperando = [f for f in filas["filas"] if not f["reminder_id"] and _fecha(f["expires_at"]) > ahora]
    confirmadas = [f for f in filas["filas"] if f["reminder_id"]]
    caducadas = [f for f in filas["filas"] if not f["reminder_id"] and _fecha(f["expires_at"]) <= ahora]
    huerfanas = [f["reminder_id"] for f in confirmadas
                 if f["reminder_id"] not in {r["id"] for r in d["tablas"]["reminders"]["filas"]}]
    detalle = (f"esperando 'sí': {len(esperando)} · confirmadas: {len(confirmadas)} · "
               f"caducadas sin limpiar: {len(caducadas)}")
    for f in esperando:
        tarea = json.loads(f["payload"]).get("tarea", "")
        detalle += f" · {f['channel']} espera «{tarea[:30]}» hasta {f['expires_at'][:16]}"
    if huerfanas:
        return "FALLA", f"confirmadas sin fila en reminders: {huerfanas} · " + detalle
    return "OK", detalle


# Prueba por el manejador real del bot (channels/assistant.py), dentro del contenedor del bot,
# con un canal simulado: no escribe a Telegram. Crea, "reinicia" (bot nuevo), confirma, repite
# el "sí" y cancela el recordatorio de prueba al final.
MANEJADOR = r'''
import asyncio, json, os, sys
from channels.assistant import Assistant
from channels.simulado import SimChannel
from channels.client import NexusClient
chat, tag = sys.argv[1], sys.argv[2]
url = os.getenv("NEXUS_API_URL", "http://core:8000")
def bot():
    ch = SimChannel([chat]); return Assistant(ch, NexusClient(url), {chat}), ch
async def say(b, ch, t):
    return "\n".join(await b.handle(ch.text(t, user=chat)))
async def main():
    out = {}
    b, ch = bot()
    out["pedir"] = await say(b, ch, f"recuérdame mañana a las 9 revisar informe {tag}")
    b, ch = bot()  # bot nuevo: nada en memoria, como tras un reinicio
    out["confirmar"] = await say(b, ch, "Sí, guárdalo")
    out["repetir"] = await say(b, ch, "sí")
    filas = [r for r in await b.nexus.reminders(chat) if tag in r["text"]]
    out["filas"] = filas
    for r in filas:
        await b.nexus._req("POST", f"/reminders/{r['id']}/cancel")
    print(json.dumps(out, ensure_ascii=False))
asyncio.run(main())
'''


def t_recordatorio_manejador():
    chat = f"informe-{RUN}"
    p = subprocess.run(["docker", "exec", "-i", "nexus-telegram", "python", "-", chat, RUN],
                       input=MANEJADOR, capture_output=True, text=True, timeout=300)
    if p.returncode:
        return "FALLA", (p.stderr.strip().splitlines() or ["sin salida"])[-1][:150]
    d = json.loads(p.stdout.strip().splitlines()[-1])
    filas = d["filas"]
    if "¿Lo guardo?" not in d["pedir"]:
        return "FALLA", f"no pidió confirmación: {d['pedir'][:80]!r}"
    if "Guardado (n.º" not in d["confirmar"]:
        return "FALLA", f"tras 'reiniciar' no guardó: {d['confirmar'][:80]!r}"
    if len(filas) != 1:
        return "FALLA", f"se esperaba 1 fila y hay {len(filas)} (el 'sí' repetido duplicó o no guardó)"
    return "OK", f"pedir → reinicio → sí → fila {filas[0]['id']} ({filas[0]['due_local']}, hora de Perú) → sí repetido sin duplicar → cancelada"


def t_respaldo():
    b = call("/backup")
    return ("OK" if b.get("ultimo_respaldo") else "AVISO"), f"último respaldo: {b.get('ultimo_respaldo')}"


for n, f in [
    ("Servicio y modelo activo", t_salud),
    ("Guardar una nota y responder con ella", t_guardar_y_preguntar),
    ("Decir 'no sé' cuando no sabe", t_no_se),
    ("Procesamiento sin fallos acumulados", t_worker),
    ("Bot de Telegram encendido", t_telegram),
    ("Cola de salida a Telegram", t_salida),
    ("Recordatorios: todas las tablas, ¿alguno vencido sin enviar?", t_recordatorios),
    ("Recordatorios: confirmaciones pendientes", t_confirmaciones),
    ("Recordatorio creado por el manejador real del bot", t_recordatorio_manejador),
    ("Respaldos", t_respaldo),
]:
    prueba(n, f)

print(f"\nINFORME NEXUS · corrida {RUN}\n" + "-" * 70)
for n, e, t, d in filas:
    print(f"{e:6} {n} ({t})\n       {d}")
print("-" * 70)
print("""
LO QUE PUEDE (si arriba salió OK): guardar notas, PDF, audio y archivos; responder con esos datos
citando fuentes; decir que no sabe; seguir funcionando si el modelo se cae; hacer respaldos.

LO QUE NO PUEDE HOY:
 - Llamarte por teléfono (Telegram no lo permite).
 - WhatsApp: está preparado pero apagado, exige cuenta de Meta Business.
 - Juntar varias notas con fiabilidad (listas de clientes, totales): acierta unas 2 de 4.
 - Usar Gemini: su clave daba error 401.
 - Garantía de privacidad con la nube encendida: lo que preguntes viaja a Groq.
 - Groq gratis se agota tras unas 11 preguntas seguidas y cae al modelo local, más lento.
""")
