"""Evaluación de recuperación y respuesta con notas de ventas sintéticas (solo librería estándar).

Uso:
    python3 tests/eval_ventas.py [--url http://localhost:8000] [--label nombre]
                                 [--nube] [--candidatos N] [--image nexus-core:otra]

Sin --url usa una instancia temporal aislada (como tests/e2e.py): solo estarán las 20 notas
de la corrida. La nube queda APAGADA salvo con --nube (usa las keys de .env; datos sintéticos).
Con --url las notas quedan guardadas en esa instancia y se usa su configuración.

20 notas inventadas, con distractores y nombres parecidos entre clientes. 14 preguntas con otras
palabras: 8 con respuesta en una nota, 4 que juntan varias (una es la regresión "Gil Maruri":
el modelo inventó ese cliente) y 2 sin respuesta. Por pregunta mide: nota correcta en el top 3
de /search y su distancia, palabra clave en /ask, "no sé" cuando corresponde, datos inventados
(nombres o cifras que no están en ninguna nota y que NEXUS no marcó con ⚠) y el tiempo de /ask.
El resultado se guarda en tests/eval_last.json bajo --label para comparar corridas.
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
from core.verify import unverified  # noqa: E402  (solo librería estándar)
from tests.e2e import NO_INFO, Api, isolated_instance  # noqa: E402

OUT = REPO / "tests" / "eval_last.json"
WAIT_TIMEOUT = 15 * 60
TOP = 3
BACKLOG_WARN = 20
NO_KNOW = (NO_INFO.lower(), "no tengo informacion", "no lo se", "no encuentro", "no dispongo")
WARNING = "⚠ No pude verificar:"

NOTES = [
    # 0-11: notas con respuesta
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
    # 12-19: distractores con nombres parecidos
    "Rosario Quispe pidió cotización de 5 monitores Samsung de 24 pulgadas; todavía no confirma.",
    "Gilmer Mamani compró 6 mouse inalámbricos y pagó 180 soles en efectivo.",
    "Ferretería Los Álamos pagó completo su pedido de 12 extensiones eléctricas.",
    "Bodega El Trigo pidió 15 cajas de aceite para el miércoles.",
    "La Clínica San Rafael Norte compró 2 impresoras Epson L3250 al contado.",
    "Marisela Huamán debe 320 soles de una silla ergonómica; paga a fin de mes.",
    "El Colegio San Martín pidió 10 proyectores Epson para sus aulas. Entrega en noviembre.",
    "Teófilo Cáceres pide que no lo llamen antes de las 9 de la mañana.",
]

# (tipo, pregunta, notas con la respuesta, claves). Cada grupo de claves debe aparecer en la
# respuesta; basta una alternativa del grupo. Sin notas = la memoria no tiene la respuesta.
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
     [("rosaura",), ("gilberto",), ("brasa",)]),
    ("varias", "¿Qué clientes nos deben dinero o pagan en partes?", [2, 8, 17],
     [("andes",), ("ursula",), ("marisela",)]),
    ("varias", "¿Qué hay anotado sobre productos Epson?", [4, 16, 18],
     [("780",), ("rafael norte",), ("san martin",)]),
    ("varias", "¿A qué clientes hay que llamar con cuidado por la hora?", [9, 19],
     [("teodoro",), ("teofilo",)]),
    ("ninguna", "¿Cuál es el horario de atención de la tienda los domingos?", [], []),
    ("ninguna", "¿Cuántas tablets Samsung vendimos en julio?", [], []),
]
REGRESSION = "¿Qué clientes nos deben dinero o pagan en partes?"  # inventó "Gil Maruri"


def norm(text: str) -> str:
    text = (text or "").replace("\u202f", " ").replace("\u00a0", " ").replace("*", "")
    text = unicodedata.normalize("NFD", text.lower())
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


def evaluate(api: Api, url: str, label: str, isolated: bool, extra: dict) -> int:
    health = api.ok("GET", "/health")
    cola = api.ok("GET", "/worker").get("eventos_por_estado", {}).get("pending", 0)
    if cola > BACKLOG_WARN:
        print(f"Aviso: el worker tiene {cola} elementos en cola; la espera puede ser larga.")

    rid = uuid.uuid4().hex[:10]
    print(f"NEXUS eval ventas · corrida {rid} · {url} · etiqueta '{label}' {extra or ''}\n")
    ids = []
    for note in NOTES:
        r = api.ok("POST", "/inbox/text", {"text": f"{note} [eval {rid}]", "source": "eval"})
        ids.append(r["id"])
    secs = wait_processed(api, ids)
    print(f"{len(NOTES)} notas procesadas en {secs:.0f} s\n")

    rows = []
    for kind, question, expected, keys in QUESTIONS:
        hits = api.ok("GET", f"/search?q={urllib.parse.quote(question)}&k=10")
        top = [note_of(h) for h in hits[:TOP]]
        dist: dict[int, float] = {}
        for h in hits:
            n = note_of(h)
            if n is not None and h.get("distance") is not None:
                dist[n] = min(dist.get(n, 9.0), h["distance"])
        t0 = time.monotonic()
        resp = api.ok("POST", "/ask", {"question": question})
        seconds = time.monotonic() - t0
        answer = (resp.get("answer") or "").strip()
        body, _, flagged = answer.partition(WARNING)
        no = says_no(answer)
        row = {
            "tipo": kind,
            "pregunta": question,
            "top1_distancia": hits[0]["distance"] if hits else None,
            "respuesta": answer[:400],
            "segundos": round(seconds, 2),
            "proveedor": (resp.get("llm") or {}).get("proveedor"),
            "dijo_no_se": no,
            # Nombres o cifras que no están en ninguna nota y que NEXUS no marcó con ⚠
            "inventados": [] if no else unverified(body, NOTES + [f"eval {rid}"], question),
            "regenerada": bool((resp.get("llm") or {}).get("regenerada")),
            "marcados": [x.strip() for x in flagged.split(",") if x.strip()],
        }
        if expected:
            row["recuperada"] = all(n in top for n in expected)
            row["distancias"] = [dist.get(n) for n in expected]
            row["respuesta_ok"] = all(any(norm(k) in norm(body) for k in group) for group in keys)
        else:
            row["respuesta_ok"] = no
        rows.append(row)

    answerable = [r for r in rows if r["tipo"] != "ninguna"]
    unanswerable = [r for r in rows if r["tipo"] == "ninguna"]
    multi = [r for r in rows if r["tipo"] == "varias"]
    hit_d = [d for r in answerable if r["recuperada"] for d in r["distancias"] if d is not None]
    miss_d = [r["top1_distancia"] for r in unanswerable if r["top1_distancia"] is not None]
    low = max(hit_d) if hit_d else None
    high = min(miss_d) if miss_d else None
    times = [r["segundos"] for r in rows]
    regression = next(r for r in rows if r["pregunta"] == REGRESSION)
    totals = {
        "recuperacion": f"{sum(r['recuperada'] for r in answerable)}/{len(answerable)}",
        "respuesta": f"{sum(r['respuesta_ok'] for r in answerable)}/{len(answerable)}",
        "sintesis": f"{sum(r['respuesta_ok'] for r in multi)}/{len(multi)}",
        "sin_respuesta_ok": f"{sum(r['respuesta_ok'] for r in unanswerable)}/{len(unanswerable)}",
        "no_se_teniendo_respuesta": sum(r["dijo_no_se"] for r in answerable),
        "inventados": sum(len(r["inventados"]) for r in rows),
        "marcados": sum(len(r["marcados"]) for r in rows),
        "regeneradas": sum(r.get("regenerada", False) for r in rows),
        "regresion_gil_maruri": "ok" if not regression["inventados"] else "inventó",
        "ask_promedio_s": round(sum(times) / len(times), 2),
        "ask_max_s": round(max(times), 2),
        "max_distancia_acierto": low,
        "min_distancia_sin_respuesta": high,
        "rango_sugerido_max_distance": [low, high] if low is not None and high is not None and low < high else None,
    }

    # ---------- tabla ----------
    print(f"{'#':>2}  {'tipo':7}  {'top3':4}  {'distancia':20}  {'resp':4}  {'s':>5}  pregunta")
    print("-" * 112)
    for i, r in enumerate(rows, 1):
        if r["tipo"] == "ninguna":
            top3, d = "—", f"top1 {r['top1_distancia']}"
        else:
            top3 = "sí" if r["recuperada"] else "no"
            d = ",".join("—" if x is None else f"{x:.3f}" for x in r["distancias"])
        resp = ("sí" if r["respuesta_ok"] else "no") + ("*" if r["dijo_no_se"] and r["tipo"] != "ninguna" else "")
        print(f"{i:>2}  {r['tipo']:7}  {top3:4}  {d:20}  {resp:4}  {r['segundos']:5.1f}  {r['pregunta']}")
        if not r["respuesta_ok"] or r["inventados"] or r["marcados"]:
            print(f"{'':49}↳ {r['respuesta'][:110]!r}")
        if r["inventados"]:
            print(f"{'':49}  inventado: {', '.join(r['inventados'])}")
    print("-" * 112)
    print("* = dijo que no sabe teniendo la respuesta\n")
    print(f"Recuperación (nota correcta en top {TOP}): {totals['recuperacion']}")
    print(f"Respuesta con la palabra clave:          {totals['respuesta']} (síntesis {totals['sintesis']})")
    print(f"Sin respuesta y dijo que no sabe:        {totals['sin_respuesta_ok']}")
    print(f"'No tengo información' teniendo la respuesta: {totals['no_se_teniendo_respuesta']}")
    print(f"Datos inventados sin aviso: {totals['inventados']} · marcados con ⚠: {totals['marcados']}"
          f" · regeneradas: {totals['regeneradas']} · regresión Gil Maruri: {totals['regresion_gil_maruri']}")
    print(f"/ask: promedio {totals['ask_promedio_s']} s, máximo {totals['ask_max_s']} s")
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
        "opciones": extra,
        "modelo": health.get("llm_model"),
        "corrida": rid,
        "notas": len(NOTES),
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
    p.add_argument("--nube", action="store_true", help="instancia aislada con el modo nube de .env")
    p.add_argument("--candidatos", type=int, help="NEXUS_ASK_CANDIDATES (y _LIST) de la instancia aislada")
    p.add_argument("--image", help="imagen de la instancia aislada (p. ej. una versión anterior)")
    args = p.parse_args()
    if args.url:
        return evaluate(Api(args.url), args.url, args.label, isolated=False, extra={})
    env = {"NEXUS_WORKER_INTERVAL": "3", "NEXUS_ALLOW_CLOUD": "on" if args.nube else "off"}
    if args.candidatos:
        env["NEXUS_ASK_CANDIDATES"] = env["NEXUS_ASK_CANDIDATES_LIST"] = str(args.candidatos)
    extra = {k: v for k, v in (("nube", args.nube), ("candidatos", args.candidatos), ("imagen", args.image)) if v}
    with isolated_instance(env, image=args.image) as url:
        return evaluate(Api(url), url, args.label, isolated=True, extra=extra)


if __name__ == "__main__":
    sys.exit(main())
