"""Una semana simulada de saludos de Isabelle (7 de mañana y 7 de noche) para juzgar la
naturalidad y que no se repitan. Datos inventados; OpenRouter real (centavos).

    python3 tests/muestra_iniciativa.py

Corre en un contenedor temporal con base propia en /tmp (la memoria real no se toca), con la imagen
de NEXUS_E2E_IMAGE (o la del núcleo en marcha) y el reloj simulado. Escribe tests/muestra_iniciativa.md
(ignorado por git).
"""

import asyncio
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "muestra_iniciativa.md"
LUNES = date(2026, 11, 2)
FICHA = {
    "personas": ["Piter: proveedor de laptops", "Ana: socia en la tienda", "Lucía: su hermana"],
    "negocio": ["Tiene una tienda de laptops y accesorios"],
    "preferencias": ["Prefiere mensajes cortos"],
    "habitos": ["Sale a correr los sábados temprano"],
    "metas": ["Abrir un segundo local este año"],
    "pendientes": [], "datos": [],
}
# Por día: (resumen de la charla, recordatorios [(día, h, m, texto, «hecho»|«sin_confirmar»|None)], notas del día)
SEMANA = [
    ("Empezó la semana organizando pedidos con Piter.",
     [(0, 9, 0, "llamar a Piter", "hecho"), (0, 16, 0, "reunión con Ana", None)],
     ["Rosa Quispe pagó 50 soles de la impresora"]),
    ("Habló de abrir un segundo local y de buscar un lugar en el centro.", [], []),
    ("Estuvo preparando la cotización para Tralvex.",
     [(1, 18, 0, "enviar la cotización a Tralvex", "sin_confirmar"), (2, 11, 30, "pagar el alquiler", "hecho"),
      (3, 8, 0, "recoger el pedido de Piter", None)],
     ["Precio nuevo de la IdeaPad 5: 2 450 soles"]),
    ("Día cargado: pedido, almuerzo con su hermana y cierre de caja.",
     [(3, 13, 0, "almuerzo con Lucía", "hecho"), (3, 18, 30, "cerrar caja", "hecho")],
     ["Llegaron 10 laptops del pedido de Piter"]),
    ("Contó que está cansado por la semana.", [(5, 10, 0, "hacer inventario", None)], []),
    ("Sábado de inventario; salió a correr temprano.", [], ["Inventario: faltan 3 mouse inalámbricos"]),
    ("Domingo de descanso con la familia.", [(7, 9, 0, "reunión con el contador", None)], []),
]


def afuera() -> int:
    sys.path.insert(0, str(REPO))
    from tests.e2e import _imagen

    cmd = ["docker", "run", "--rm"]
    if (REPO / ".env").exists():
        cmd += ["--env-file", str(REPO / ".env")]
    cmd += ["-e", "NEXUS_DATA_DIR=/tmp/muestra", "-e", "NEXUS_DAILY_BUDGET_USD=",
            "-v", f"{REPO / 'tests'}:/app/tests", _imagen(), "python", "-m", "tests.muestra_iniciativa", "--dentro"]
    return subprocess.run(cmd).returncode


def adentro() -> int:
    from unittest import mock

    from core import charla, clock, db, iniciativa, reminders

    db.init_db()
    ahora = {"t": None}
    mock.patch.object(clock, "now_utc", lambda: ahora["t"]).start()

    def en(d: date, h: int, m: int = 0) -> None:
        ahora["t"] = clock.local_to_utc(datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m))

    rechazos: list[tuple[str, str]] = []
    original = iniciativa.verificar

    def verificar(texto, *a):
        motivo = original(texto, *a)
        if motivo:
            rechazos.append((" ".join(texto.split()), motivo))
        return motivo

    mock.patch.object(iniciativa, "verificar", verificar).start()
    en(LUNES - timedelta(days=1), 20)
    iniciativa.al_escribir("simulado", "1001")
    with db.connect() as c:
        charla._set(c, charla.K_FICHA, __import__("json").dumps(FICHA, ensure_ascii=False))
    lineas = ["# Muestra de iniciativa: una semana de saludos", "",
              "Datos inventados; reloj simulado; OpenRouter real. Cada saludo dice si lo redactó la IA o si",
              "se usó una frase de respaldo (y por qué).", ""]
    resultados = []
    for i, (resumen, recs, notas) in enumerate(SEMANA):
        dia = LUNES + timedelta(days=i)
        en(dia, 6, 0)
        hechos = []
        for (d, h, m, texto, final) in recs:
            due = clock.local_to_utc(datetime.combine(LUNES + timedelta(days=d), datetime.min.time()).replace(hour=h, minute=m))
            r = reminders.create(texto, due.isoformat(), "simulado", "1001")
            if final == "sin_confirmar":
                with db.connect() as c:
                    c.execute("UPDATE reminders SET estado = 'sin_confirmar', proximo_envio = NULL WHERE id = ?", (r["id"],))
            elif final == "hecho":  # lo cierra a su hora, después del saludo de la mañana
                hechos.append((r["id"], h, m))
        lineas += [f"## {['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'][i]} {dia.day}", "",
                   f"*Contexto:* {resumen} Recordatorios: {', '.join(t for *_, t, _ in recs) or 'ninguno'}."
                   f" Notas: {'; '.join(notas) or 'ninguna'}.", ""]
        for tipo, (h, m) in (("manana", (7, 30)), ("noche", (21, 30))):
            if tipo == "noche":
                for rid, hh, mm in hechos:
                    en(dia, hh, mm)
                    reminders.cerrar(rid, "hecho")
                en(dia, 14, 0)
                with db.connect() as c:
                    charla._set(c, charla.K_RESUMEN, resumen)  # de lo que hablaron hoy (vale hasta mañana)
                    for n, nota in enumerate(notas):
                        eid = f"muestra-{i}-{n}"
                        c.execute("INSERT INTO events (id, created_at, kind, source, filename, size, sha256, raw_path, status)"
                                  " VALUES (?, ?, 'text', 'muestra', 'nota.txt', 1, ?, '-', 'processed')",
                                  (eid, ahora["t"].isoformat(), eid))
                        c.execute("INSERT INTO chunks (event_id, position, content) VALUES (?, 0, ?)", (eid, nota))
            en(dia, h, m)
            rechazos.clear()
            hecho = asyncio.run(iniciativa.due_step()).get("saludos", {})
            fila = iniciativa._fila(dia, tipo)
            texto = (fila or {}).get("texto") or f"(sin saludo: {(fila or {}).get('motivo')})"
            fuente = hecho.get(tipo, "-")
            detalle = (fila or {}).get("detalle")
            resultados.append((tipo, fuente, texto))
            lineas += [f"**{'Mañana' if tipo == 'manana' else 'Noche'}** ({fuente}{', ' + detalle if detalle else ''}):", "",
                       "> " + texto.replace("\n", "\n> "), ""]
            lineas += [f"*La IA había escrito (rechazado por {mot}):* {txt}" for txt, mot in rechazos] + ([""] if rechazos else [])
            en(dia, h + 1, m)
            iniciativa.al_escribir("simulado", "1001", "gracias")  # respondió: la cuenta no sube
    arranques = [iniciativa.arranque(t) for _, _, t in resultados]
    with db.connect() as c:
        costo = c.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c, COUNT(*) AS n FROM llm_usage").fetchone()
    lineas += ["## Cifras", "",
               f"- Saludos: {len(resultados)}; redactados por la IA: {sum(f == 'ia' for _, f, _ in resultados)};"
               f" con respaldo: {sum(f == 'respaldo' for _, f, _ in resultados)}.",
               f"- Arranques distintos: {len(set(arranques))} de {len(arranques)} ({', '.join(arranques)}).",
               f"- Llamadas a OpenRouter: {costo['n']}; costo: {costo['c']:.5f} USD.", ""]
    OUT.write_text("\n".join(lineas), encoding="utf-8")
    print(f"Escrito {OUT.name}: {len(resultados)} saludos, {costo['c']:.5f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(adentro() if "--dentro" in sys.argv else afuera())
