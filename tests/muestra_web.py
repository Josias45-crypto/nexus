"""Muestra de la web (Fase 5) con capturas reales, con datos inventados.

    NEXUS_E2E_IMAGE=nexus-core:prueba python3 tests/muestra_web.py

Levanta una instancia aislada (la memoria real no se toca), le enseña unas notas y una foto
sintéticas, conversa por la web y toma capturas en un Chromium sin interfaz dentro de Docker
(imagen de Playwright fijada en PLAYWRIGHT). Escribe tests/muestra_web.md y tests/muestra_web/*.png
(ignorados por git). Usa OpenRouter real (unos centavos de dólar: una foto y unas respuestas).
"""

import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests import fixtures  # noqa: E402
from tests.e2e import E2E_NAME, E2E_PORT, ISOLATED_ENV, Api, event_id, isolated_instance, wait_status  # noqa: E402

PLAYWRIGHT = "mcr.microsoft.com/playwright/python:v1.49.0-noble"
OUT = REPO / "tests" / "muestra_web.md"
CAPTURAS = REPO / "tests" / "muestra_web"
NOTAS = [
    "Rosa Quispe se llevó una impresora Epson L3250 a 450 soles; paga 50 cada viernes.",
    "Tralvex es mi proveedor de laptops Lenovo y entrega los martes.",
    "El local nuevo sería en la avenida España; el alquiler pedido es 1 800 soles.",
    "Idea de campaña: sorteo de audífonos entre quienes paguen por Yape en diciembre.",
    "La Clínica San Rafael pidió cotización de mantenimiento para 12 equipos.",
    "Bodega El Trigal paga al contado; el contacto es Hernán.",
]

# Se ejecuta dentro del contenedor de Playwright (red del anfitrión: ve 127.0.0.1:8001)
NAVEGADOR = r'''
import sys, time
from playwright.sync_api import sync_playwright
url, clave, salida = sys.argv[1], sys.argv[2], sys.argv[3]
with sync_playwright() as p:
    b = p.chromium.launch()
    def entrar(ctx):
        pg = ctx.new_page(); pg.goto(url + "/login"); pg.fill("#c", clave); pg.click("#b")
        pg.wait_for_url(url + "/"); return pg
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
    pg = entrar(ctx)
    time.sleep(4); pg.screenshot(path=f"{salida}/chat.png")
    pg.fill("#txt", "¿Cuánto me debe Rosa?"); pg.press("#txt", "Enter")
    pg.wait_for_function("document.querySelectorAll('.isa').length >= 4", timeout=60000); time.sleep(0.6)
    pg.screenshot(path=f"{salida}/chat_respuesta.png")
    pg.add_style_tag(content="#chat,#top{display:none!important}"); time.sleep(1.5)
    pg.screenshot(path=f"{salida}/neuronas.png")
    pg.goto(url + "/guardado"); pg.wait_for_selector(".item"); time.sleep(0.5)
    pg.screenshot(path=f"{salida}/guardado.png", full_page=True)
    pg.click(".item >> nth=0"); pg.wait_for_selector("#det h2"); time.sleep(1)
    pg.screenshot(path=f"{salida}/guardado_detalle.png", full_page=True)
    movil = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pm = entrar(movil); time.sleep(3); pm.screenshot(path=f"{salida}/movil.png")
    out = b.new_context(viewport={"width": 1280, "height": 800}).new_page(); out.goto(url + "/login"); time.sleep(0.5)
    out.screenshot(path=f"{salida}/login.png")
    b.close()
'''


def main() -> int:
    if CAPTURAS.exists():
        shutil.rmtree(CAPTURAS)
    CAPTURAS.mkdir(parents=True)
    with isolated_instance(ISOLATED_ENV) as url:
        api = Api(url)
        for nota in NOTAS:
            wait_status(api, event_id(api.ok("POST", "/inbox/text", {"text": nota, "source": "muestra"})), 180)
        foto = fixtures.image_with_text(["BOLETA B001-4517", "Impresora Epson L3250", "TOTAL S/ 450.00"])
        r = api.upload("boleta.png", foto, "image/png")
        api.ok("POST", "/conversar/archivo", {"archivos": [{"id": r["id"], "filename": "boleta.png", "kind": r["kind"],
                                                            "duplicate": False}], "channel": "web", "chat_id": "web"})
        for texto in ("hola Isabelle", "recuerda esto: el sábado llega mercadería nueva de Tralvex"):
            api.ok("POST", "/conversar", {"mensajes": [texto], "channel": "web", "chat_id": "web"})
        time.sleep(8)  # el worker termina de procesar y de resumir
        clave = "muestra-" + uuid.uuid4().hex[:10]
        subprocess.run(["docker", "exec", "-i", E2E_NAME, "python", "-c",
                        "import sys; from core import auth; auth.fijar_clave(sys.stdin.read().strip())"],
                       input=clave, text=True, check=True, capture_output=True)
        r = subprocess.run(["docker", "run", "--rm", "--network", "host", "-i", "-v", f"{CAPTURAS}:/salida",
                            "--user", "root", PLAYWRIGHT, "python", "-", f"http://127.0.0.1:{E2E_PORT}", clave, "/salida"],
                           input=NAVEGADOR, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise SystemExit("el navegador falló: " + r.stderr[-800:])
        subprocess.run(["docker", "run", "--rm", "-v", f"{CAPTURAS}:/salida", PLAYWRIGHT, "chown", "-R",
                        f"{_uid()}:{_gid()}", "/salida"], capture_output=True)
        panel = api.ok("GET", "/web/panel")
    pngs = sorted(p.name for p in CAPTURAS.glob("*.png"))
    titulos = {"login.png": "Entrada (sin sesión todo redirige aquí)", "chat.png": "Chat sobre las neuronas (escritorio)",
               "chat_respuesta.png": "Pregunta y respuesta: destellos al buscar y responder",
               "neuronas.png": "Solo las neuronas: notas, archivos y conceptos reales con sus conexiones",
               "guardado.png": "Lo guardado", "guardado_detalle.png": "Detalle: análisis de la foto y descarga del original",
               "movil.png": "En celular (390 × 844)"}
    lineas = ["# Muestra: la web de Isabelle (Fase 5)", "",
              f"Generado el {time.strftime('%Y-%m-%d %H:%M')} en una instancia aislada con datos inventados "
              f"({len(NOTAS)} notas, una foto y una charla corta). Capturas de Chromium sin interfaz ({PLAYWRIGHT}).",
              f"Panel: {panel['dias_de_vida']} día(s) de vida, {panel['elementos']} elementos, {panel['gasto_hoy_usd']} USD hoy.", ""]
    for nombre in ["login.png", "chat.png", "chat_respuesta.png", "neuronas.png", "guardado.png", "guardado_detalle.png", "movil.png"]:
        if nombre in pngs:
            lineas += [f"## {titulos[nombre]}", "", f"![{titulos[nombre]}](muestra_web/{nombre})", ""]
    OUT.write_text("\n".join(lineas), encoding="utf-8")
    print(f"Escrito {OUT.relative_to(REPO)} con {len(pngs)} capturas")
    return 0


def _uid() -> int:
    import os

    return os.getuid()


def _gid() -> int:
    import os

    return os.getgid()


if __name__ == "__main__":
    sys.exit(main())
