"""Recalibra NEXUS_MAX_DISTANCE con los datos reales (solo lectura; librería estándar).

Uso:
    python3 tests/calibrate.py [--url http://localhost:8000]

- Positivas: por cada documento digerido, preguntas con sus conceptos ("¿Qué sabes de X?"),
  y por cada nota real, una pregunta con 3 de sus palabras clave. La distancia es la del
  mejor trozo de ESE documento entre los resultados.
- Negativas: temas que no deberían estar en la memoria. La distancia es la del mejor trozo.
- Regla de NEXUS: un trozo se usa si distancia <= T, o <= T + margen si además coincide por
  palabras. Propone (T, margen) que maximiza aciertos − falsos positivos.

Las positivas salen del propio documento (son optimistas): úsalo como guía, no como verdad.
"""

import argparse
import json
import re
import statistics
import urllib.parse
import urllib.request
from datetime import date, timedelta

NEGATIVES = [
    "¿Cuál es la capital de Mongolia?",
    "¿Cómo se hace una pizza napolitana?",
    "¿Quién ganó el mundial de fútbol de 1986?",
    "¿Cuántos huesos tiene el cuerpo humano?",
    "¿Cómo se cambia el aceite de una motocicleta?",
    "¿Qué es la fotosíntesis?",
    "¿Cuál es la distancia de la Tierra a la Luna?",
    "¿Cómo se juega al ajedrez?",
    "¿Qué idioma se habla en Islandia?",
    "¿Cuál es la fórmula química del agua oxigenada?",
    "¿Cómo se poda un rosal?",
    "¿Quién escribió Cien años de soledad?",
    "¿Qué temperatura hace en la Antártida?",
    "¿Cómo funciona un motor de combustión?",
    "¿Cuál es el río más largo de África?",
    "¿Qué es el teorema de Pitágoras?",
    "¿Cómo se prepara el sushi?",
    "¿Cuántos planetas tiene el sistema solar?",
    "¿Qué hace un electricista?",
    "¿Cuándo fue la revolución francesa?",
]


def get(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def search(base: str, q: str, k: int = 10) -> list[dict]:
    return get(f"{base}/search?k={k}&q={urllib.parse.quote(q)}")


TEST_MARKERS = re.compile(r"e2e|sint[eé]tic|prueba|ficticio|zorvak|melivora|brumaleon", re.I)
STOP = set("para pero como esta este tiene sobre entre desde donde cuando porque cada muy más mas "
           "también solo puede pueden hace hacer todo todos otra otro sus los las del una unos".split())


def keywords(text: str, n: int = 3) -> list[str]:
    words = [w for w in re.findall(r"[a-záéíóúñ]+", text.lower()) if len(w) >= 5 and w not in STOP]
    return list(dict.fromkeys(words))[:n]


def real_notes(base: str, days: int) -> list[dict]:
    notes = []
    today = date.today()
    for i in range(days):
        day = (today - timedelta(days=i)).isoformat()
        for e in get(f"{base}/today?dia={day}")["elementos"]:
            text = f"{e['archivo']} {e['resumen']}"
            if e["estado"] == "processed" and not e["privado"] and not TEST_MARKERS.search(text):
                notes.append(e)
    return notes


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--max-docs", type=int, default=40)
    p.add_argument("--incluir-e2e", action="store_true", help="incluir documentos sintéticos del e2e")
    p.add_argument("--dias", type=int, default=30, help="días de notas reales a usar")
    args = p.parse_args()
    base = args.url.rstrip("/")

    docs = get(f"{base}/knowledge?limit=500")["resumenes"]
    if not args.incluir_e2e:
        docs = [d for d in docs if not (d.get("filename") or "").startswith("e2e")]
    docs = docs[: args.max_docs]

    def best(hits):
        hits = [h for h in hits if h["distance"] is not None]
        return min(hits, key=lambda h: h["distance"]) if hits else None

    # Positivas: (distancia, coincide por palabras) del mejor trozo del documento correcto
    pos: list[tuple[float, bool]] = []
    queries = [(f"¿Qué sabes de {c}?", d["event_id"]) for d in docs for c in d["concepts"][:3]]
    for e in real_notes(base, args.dias):
        kw = keywords(e["resumen"])
        if len(kw) >= 2:
            queries.append(("¿Qué sé de " + " ".join(kw) + "?", e["id"]))
    for q, eid in queries:
        h = best([h for h in search(base, q) if h["event_id"] == eid])
        pos.append((h["distance"], bool(h.get("coincide_texto"))) if h else (2.0, False))
    # Negativas: todos los trozos devueltos (basta uno que pase para ser falso positivo)
    neg = [[h for h in search(base, q) if h["distance"] is not None] for q in NEGATIVES]

    if len(pos) < 8:
        print(f"Solo {len(pos)} pregunta(s) positiva(s): hace falta más memoria real para calibrar.")
        return 1

    def passes(d: float, fts: bool, t: float, m: float) -> bool:
        return d <= t or (fts and d <= t + m)

    xs = sorted(d for d, _ in pos)
    ns = sorted(min((h["distance"] for h in hs), default=2.0) for hs in neg)
    print(f"Documentos digeridos: {len(docs)} · preguntas positivas: {len(pos)} · negativas: {len(neg)}\n")
    print(f"positivas  mín {xs[0]:.3f}  mediana {statistics.median(xs):.3f}  máx {xs[-1]:.3f}")
    print(f"negativas  mín {ns[0]:.3f}  mediana {statistics.median(ns):.3f}  máx {ns[-1]:.3f}\n")
    print("umbral  margen  aciertos  falsos+   neto")
    best_combo = (-1.0, 0.0, 0.0, 0.0)  # (neto, aciertos, T, margen): empate -> más aciertos
    for ti in range(70, 87, 2):
        for m in (0.0, 0.02, 0.04, 0.06, 0.08):
            t = ti / 100
            tp = sum(passes(d, f, t, m) for d, f in pos) / len(pos)
            fp = sum(any(passes(h["distance"], h.get("coincide_texto"), t, m) for h in hs) for hs in neg) / len(neg)
            if (round(tp - fp, 6), round(tp, 6)) > (round(best_combo[0], 6), round(best_combo[1], 6)):
                best_combo = (tp - fp, tp, t, m)
            if m in (0.0, 0.04):
                print(f"{t:6.2f}  {m:6.2f}  {tp:7.0%}  {fp:7.0%}  {tp - fp:5.0%}")
    net, _, t, m = best_combo
    print(f"\nSugerido: NEXUS_MAX_DISTANCE={t:.2f}  NEXUS_FTS_MARGIN={m:.2f}  (neto {net:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
