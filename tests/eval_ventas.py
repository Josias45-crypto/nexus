"""Evaluación de recuperación y respuesta con notas de ventas sintéticas (solo librería estándar).

Uso:
    python3 tests/eval_ventas.py [--url http://localhost:8000] [--label nombre]

Sin --url usa una instancia temporal aislada (como tests/e2e.py, con la nube apagada): solo
estarán las 12 notas de la corrida. Con --url las notas quedan guardadas en esa instancia.

Guarda 12 notas inventadas, espera a que se procesen y hace 12 preguntas con otras palabras:
8 con respuesta en una nota, 2 que juntan varias y 2 sin respuesta. Mide si la nota correcta
sale en el top 3 de /search (y su distancia), si /ask contiene la palabra clave y si dice que
no sabe cuando corresponde. El resultado se guarda en tests/eval_last.json bajo --label para
comparar antes y después de un cambio.
"""

import argparse
import json
import sys
import time
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.e2e import NO_INFO, Api, isolated_instance  # noqa: E402

OUT = REPO / "tests" / "eval_last.json"
WAIT_TIMEOUT = 15 * 60
TOP = 3
BACKLOG_WARN = 20
NO_KNOW = (NO_INFO.lower(), "no tengo informacion", "no lo se", "no encuentro", "no dispongo")

NOTES = [
    "Rosaura Quispe compró 3 laptops Lenovo a 2400 soles cada una el 4 de septiembre.",
    "Pedido de Bodega El Trigal: 40 sacos de harina para el lunes 13 de octubre. Pagan al contado."
    " El contacto es Hernán.",
    "Ferretería Los Andes debe 1850 soles desde agosto.",
    "Gilberto Mamani pidió 2 laptops HP para su oficina contable. Quiere factura a nombre de su"
    " empresa. La entrega es el viernes.",
    "El precio de la impresora Epson L3250 sube a 780 soles desde noviembre.",
    "Visité la Clínica San Rafael: les interesa un plan de mantenimiento anual para 12 equipos."
    " Les envié un presupuesto de 5400 soles. Responden en dos semanas.",
    "Marisol Huamán canceló su pedido de sillas ergonómicas porque encontró otro proveedor más barato.",
    "Descuento de 15% para clientes que compren más de 10 mouse inalámbricos.",
    "El Colegio Santa Úrsula pagará los 25 proyectores en tres cuotas: octubre, noviembre y"
    " diciembre. El primer pago ya llegó.",
    "Teodoro Cáceres prefiere que lo llamen después de las 6 de la tarde.",
    "Nuevo proveedor de tóner: Distribuidora Pacífico. Entrega en 48 horas y da crédito a 30 días.",
    "Restaurante La Brasa compró una laptop Asus para la caja y pidió instalar el sistema de ventas."
    " Se instaló el martes.",
]

# notas: índices de NOTES con la respuesta (vacío = sin respuesta).
# claves: cada grupo debe aparecer en la respuesta; basta una alternativa del grupo.
QUESTIONS = [
    ("una", "¿Cuánto dinero nos adeuda la ferretería?", [2], [("1850", "1.850", "1 850")]),
    ("una", "¿Qué cantidad de harina encargó la bodega?", [1], [("40", "cuarenta")]),
    ("una", "¿Cuál será el nuevo costo de la Epson L3250?", [4], [("780",)]),
    ("una", "¿Por cuánto fue la cotización que mandamos a la clínica?", [5], [("5400", "5.400", "5 400")]),
    ("una", "¿Por qué Marisol se echó para atrás con su compra?", [6], [("proveedor", "barat")]),
    ("una", "¿A qué hora conviene telefonear a Teodoro?", [9], [("6", "seis", "18")]),
    ("una", "¿En cuántos pagos va a cubrir el colegio los proyectores?", [8], [("tres", "3")]),
    ("una", "¿Qué distribuidor de tóner nos da plazo para pagar?", [10], [("pacifico",)]),
    ("varias", "¿Qué clientes pidieron laptops?", [0, 3, 11],
     [("rosaura", "quispe"), ("gilberto", "mamani"), ("brasa",)]),
    ("varias", "¿Qué clientes nos deben dinero o pagan en partes?", [2, 8],
     [("ferreteria", "andes"), ("colegio", "ursula")]),
    ("ninguna", "¿Cuál es el horario de atención de la tienda los domingos?", [], []),
    ("ninguna", "¿Cuántas tablets Samsung vendimos en julio?", [], []),
]


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def says_no(answer: str) -> bool:
    a = norm(answer)
    return any(norm(p) in a for p in NO_KNOW)


def note_of(hit: dict) -> int | None:
    """Índice de la nota a la que pertenece un trozo (también de corridas anteriores)."""
    content = norm(hit.get("content") or "")
    for i, note in enumerate(NOTES):
        if norm(note)[:50] in content:
            return i
    return None


def wait_processed(api: Api, ids: list[str]) -> float:
    start = time.monotonic()
    pending = set(ids)
    last_report = 0.0
    while pending:
        elapsed = time.monotonic() - start
        if elapsed > WAIT_TIMEOUT:
            raise SystemExit(f"Tras 15 min siguen sin procesar {len(pending)} nota(s). Revisa /worker.")
        rows = {e["id"]: e for e in api.ok("GET", "/inbox?limit=500")}
        for eid in list(pending):
            ev = rows.get(eid)
            if ev and ev["status"] == "processed":
                pending.discard(eid)
            elif ev and ev["status"] in ("failed", "unsupported", "empty"):
                raise SystemExit(f"Una nota quedó en '{ev['status']}': {ev.get('error')}")
        if pending and elapsed - last_report >= 60:
            cola = api.ok("GET", "/worker").get("eventos_por_estado", {}).get("pending", 0)
            print(f"  … {len(ids) - len(pending)}/{len(ids)} procesadas, {cola} en cola ({elapsed:.0f} s)")
            last_report = elapsed
        if pending:
            time.sleep(3)
    return time.monotonic() - start


def evaluate(api: Api, url: str, label: str, isolated: bool) -> int:
    health = api.ok("GET", "/health")
    cola = api.ok("GET", "/worker").get("eventos_por_estado", {}).get("pending", 0)
    if cola > BACKLOG_WARN:
        print(f"Aviso: el worker tiene {cola} elementos en cola; la espera puede ser larga.")

    rid = uuid.uuid4().hex[:10]
    print(f"NEXUS eval ventas · corrida {rid} · {url} · etiqueta '{label}'\n")
    ids = []
    for note in NOTES:
        r = api.ok("POST", "/inbox/text", {"text": f"{note} [eval {rid}]", "source": "eval"})
        ids.append(r["id"])
    secs = wait_processed(api, ids)
    print(f"12 notas procesadas en {secs:.0f} s\n")

    rows = []
    for kind, question, expected, keys in QUESTIONS:
        hits = api.ok("GET", f"/search?q={urllib.parse.quote(question)}&k=10")
        top = [note_of(h) for h in hits[:TOP]]
        dist: dict[int, float] = {}
        for h in hits:
            n = note_of(h)
            if n is not None and h.get("distance") is not None:
                dist[n] = min(dist.get(n, 9.0), h["distance"])
        resp = api.ok("POST", "/ask", {"question": question})
        answer = (resp.get("answer") or "").strip()
        no = says_no(answer)
        row = {
            "tipo": kind,
            "pregunta": question,
            "top1_distancia": hits[0]["distance"] if hits else None,
            "respuesta": answer[:300],
            "dijo_no_se": no,
        }
        if expected:
            row["recuperada"] = all(n in top for n in expected)
            row["distancias"] = [dist.get(n) for n in expected]
            row["respuesta_ok"] = all(any(norm(k) in norm(answer) for k in group) for group in keys)
        else:
            row["respuesta_ok"] = no
        rows.append(row)

    answerable = [r for r in rows if r["tipo"] != "ninguna"]
    unanswerable = [r for r in rows if r["tipo"] == "ninguna"]
    hit_d = [d for r in answerable if r["recuperada"] for d in r["distancias"] if d is not None]
    miss_d = [r["top1_distancia"] for r in unanswerable if r["top1_distancia"] is not None]
    low = max(hit_d) if hit_d else None
    high = min(miss_d) if miss_d else None
    totals = {
        "recuperacion": f"{sum(r['recuperada'] for r in answerable)}/{len(answerable)}",
        "respuesta": f"{sum(r['respuesta_ok'] for r in answerable)}/{len(answerable)}",
        "sin_respuesta_ok": f"{sum(r['respuesta_ok'] for r in unanswerable)}/{len(unanswerable)}",
        "no_se_teniendo_respuesta": sum(r["dijo_no_se"] for r in answerable),
        "max_distancia_acierto": low,
        "min_distancia_sin_respuesta": high,
        "rango_sugerido_max_distance": [low, high] if low is not None and high is not None and low < high else None,
    }

    # ---------- tabla ----------
    print(f"{'#':>2}  {'tipo':7}  {'top3':4}  {'distancia':20}  {'resp':4}  pregunta")
    print("-" * 106)
    for i, r in enumerate(rows, 1):
        if r["tipo"] == "ninguna":
            top3, d = "—", f"top1 {r['top1_distancia']}"
        else:
            top3 = "sí" if r["recuperada"] else "no"
            d = ",".join("—" if x is None else f"{x:.3f}" for x in r["distancias"])
        resp = ("sí" if r["respuesta_ok"] else "no") + ("*" if r["dijo_no_se"] and r["tipo"] != "ninguna" else "")
        print(f"{i:>2}  {r['tipo']:7}  {top3:4}  {d:20}  {resp:4}  {r['pregunta']}")
        if not r["respuesta_ok"]:
            print(f"{'':42}↳ {r['respuesta'][:110]!r}")
    print("-" * 106)
    print("* = dijo que no sabe teniendo la respuesta\n")
    print(f"Recuperación (nota correcta en top {TOP}): {totals['recuperacion']}")
    print(f"Respuesta con la palabra clave:          {totals['respuesta']}")
    print(f"Sin respuesta y dijo que no sabe:        {totals['sin_respuesta_ok']}")
    print(f"'No tengo información' teniendo la respuesta: {totals['no_se_teniendo_respuesta']}")
    if totals["rango_sugerido_max_distance"]:
        print(f"NEXUS_MAX_DISTANCE sugerido: entre {low:.3f} y {high:.3f}")
    else:
        print(f"Sin separación limpia: mayor distancia de acierto {low}, menor sin respuesta {high}")

    # ---------- resultado ----------
    try:
        saved = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    previous = next((v for k, v in reversed(list(saved.items())) if k != label), None)
    saved.pop(label, None)
    saved[label] = {
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": url,
        "aislada": isolated,
        "modelo": health.get("llm_model"),
        "corrida": rid,
        "segundos_procesando": round(secs),
        "totales": totals,
        "preguntas": rows,
    }
    OUT.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if previous:
        p = previous["totales"]
        print(f"\nAntes ({previous['fecha']}): recuperación {p['recuperacion']}, respuesta {p['respuesta']},"
              f" sin respuesta {p['sin_respuesta_ok']}")
    print(f"Guardado en {OUT.relative_to(REPO)} con la etiqueta '{label}'.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Evaluación de recuperación para ventas")
    p.add_argument("--url", help="instancia existente (sin esto, se usa una aislada temporal)")
    p.add_argument("--label", default="sin-etiqueta", help="nombre para comparar corridas")
    args = p.parse_args()
    if args.url:
        return evaluate(Api(args.url), args.url, args.label, isolated=False)
    # Nube apagada: se mide el modelo local, aunque .env tenga la nube encendida
    with isolated_instance({"NEXUS_ALLOW_CLOUD": "off", "NEXUS_WORKER_INTERVAL": "3"}) as url:
        return evaluate(Api(url), url, args.label, isolated=True)


if __name__ == "__main__":
    sys.exit(main())
