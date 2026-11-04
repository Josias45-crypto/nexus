"""10 casos de ejemplo de la Fase 3 (ver y oír), con datos inventados.

    NEXUS_E2E_IMAGE=nexus-core:prueba python3 tests/muestra_ver_oir.py

Levanta una instancia aislada (como tests/e2e.py; la memoria real no se toca). Las fotos se dibujan
con Pillow dentro del contenedor y las notas de voz se generan con un modelo de voz de OpenRouter
con zdr (mistralai/voxtral-mini-tts-3-0). Conversa por POST /inbox/file + /conversar/archivo y
POST /conversar, como el bot. Usa OpenRouter real: unas 9 llamadas de visión y 2 de transcripción
(menos de 0,01 USD). Escribe tests/muestra_ver_oir.md (ignorado por git).
"""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.e2e import E2E_NAME, ISOLATED_ENV, Api, isolated_instance  # noqa: E402

OUT = REPO / "tests" / "muestra_ver_oir.md"
CHAT = "muestra"

# Se ejecuta dentro del contenedor aislado: dibuja las fotos, arma un PDF escaneado de 3 páginas y
# un video mudo, y genera las voces. Devuelve todo en base64.
GENERAR = r'''
import base64, io, json, sys, httpx
from PIL import Image, ImageDraw, ImageFont
from config import settings

F = lambda n: ImageFont.load_default(size=n)

def png(img):
    b = io.BytesIO(); img.save(b, format="PNG"); return b.getvalue()

def documento(lineas, ancho=900, fondo="white", tam=34):
    img = Image.new("RGB", (ancho, 70 * len(lineas) + 60), fondo); d = ImageDraw.Draw(img)
    for i, l in enumerate(lineas):
        d.text((40, 30 + 70 * i), l, fill="black", font=F(tam))
    return img

def perro():
    img = Image.new("RGB", (800, 600), (150, 200, 240)); d = ImageDraw.Draw(img)
    d.rectangle((0, 420, 800, 600), fill=(90, 160, 70))
    d.ellipse((250, 260, 560, 430), fill=(150, 95, 45))            # cuerpo
    d.ellipse((480, 170, 640, 320), fill=(150, 95, 45))            # cabeza
    d.ellipse((470, 160, 520, 250), fill=(110, 65, 30)); d.ellipse((600, 160, 650, 250), fill=(110, 65, 30))  # orejas
    d.ellipse((530, 220, 550, 240), fill="black"); d.ellipse((585, 220, 605, 240), fill="black")  # ojos
    d.ellipse((555, 260, 580, 280), fill="black")                   # nariz
    for x in (280, 340, 450, 510):
        d.rectangle((x, 400, x + 30, 480), fill=(150, 95, 45))     # patas
    d.line((250, 330, 180, 260), fill=(150, 95, 45), width=18)     # cola
    d.text((40, 30), "Parque Kennedy", fill="white", font=F(36))
    return img

def chat():
    img = Image.new("RGB", (720, 560), (236, 229, 221)); d = ImageDraw.Draw(img)
    burbujas = [(False, "Hola, ¿tiene la laptop Lenovo IdeaPad?"), (True, "Sí, a S/ 2 450. ¿Para cuándo?"),
                (False, "Para el sábado. ¿Hace delivery a Surco?"), (True, "Sí, el delivery cuesta S/ 15.")]
    y = 30
    for mio, t in burbujas:
        x0 = 300 if mio else 30
        d.rounded_rectangle((x0, y, x0 + 390, y + 90), 14, fill=(220, 248, 198) if mio else "white")
        d.text((x0 + 14, y + 14), t[:30], fill="black", font=F(22)); d.text((x0 + 14, y + 46), t[30:], fill="black", font=F(22))
        y += 120
    return img

def producto(nombre, precio, color):
    img = Image.new("RGB", (600, 500), "white"); d = ImageDraw.Draw(img)
    d.rectangle((150, 80, 450, 330), fill=color)
    d.text((60, 370), nombre, fill="black", font=F(34)); d.text((60, 420), precio, fill="black", font=F(34))
    return img

def pdf_escaneado():
    paginas = [documento(["CONTRATO DE ALQUILER", "Local: Av. España 1432, Trujillo", "Arrendador: Inversiones Calderón SAC"]),
               documento(["CLÁUSULA SEGUNDA", "Renta mensual: S/ 1 800.00", "Pago: los días 5 de cada mes"]),
               documento(["CLÁUSULA TERCERA", "Plazo: 12 meses desde el 01/12/2026", "Garantía: dos meses de renta"])]
    b = io.BytesIO(); paginas[0].save(b, format="PDF", save_all=True, append_images=paginas[1:], resolution=100)
    return b.getvalue()

def voz(texto):
    h = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"}
    r = httpx.post("https://openrouter.ai/api/v1/audio/speech", headers=h, timeout=120, json={
        "model": "mistralai/voxtral-mini-tts-3-0", "input": texto, "voice": "voice_3150", "response_format": "mp3",
        "provider": {"zdr": True, "data_collection": "deny"}})
    r.raise_for_status(); return r.content

def silencio():
    import wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 3)
    return b.getvalue()

def video():
    import av, numpy as np
    b = io.BytesIO(); o = av.open(b, "w", format="mp4"); s = o.add_stream("mpeg4", rate=1)
    s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
    for i in range(2):
        for p in s.encode(av.VideoFrame.from_ndarray(np.full((64, 64, 3), 80 * i, np.uint8), format="rgb24")): o.mux(p)
    for p in s.encode(): o.mux(p)
    o.close(); return b.getvalue()

out = {
    "boleta": png(documento(["BOLETA DE VENTA ELECTRÓNICA", "B001-004517", "Fecha: 02/11/2026",
                             "Impresora Epson L3250    S/ 450.00", "TOTAL A PAGAR:           S/ 450.00"])),
    "factura": png(documento(["FACTURA ELECTRÓNICA F002-00093", "Distribuidora Tralvex SAC",
                              "3 laptops Lenovo IdeaPad 5", "TOTAL: S/ 7 350.00", "Vencimiento: 20/11/2026"])),
    "perro": png(perro()), "chat": png(chat()),
    "polo": png(producto("Polo algodón talla M", "S/ 35.00", (40, 90, 200))),
    "zapatillas": png(producto("Zapatillas urbanas 42", "S/ 120.00", (220, 60, 50))),
    "pdf": pdf_escaneado(),
    "voz": voz("Isabelle, recuérdame mañana a las diez de la mañana llamar al contador para lo del IGV."),
    "silencio": silencio(), "video": video(),
}
json.dump({k: base64.b64encode(v).decode() for k, v in out.items()}, sys.stdout)
'''

# (título, turnos). Un turno es texto o ("archivo", [(clave, nombre, mime), ...], leyenda)
CASOS = [
    ("Boleta: lee la cifra y pide confirmar; «sí»",
     [("archivo", [("boleta", "boleta.png", "image/png")], ""), "sí"]),
    ("Factura con vencimiento: la corrige", [("archivo", [("factura", "factura.png", "image/png")], ""), "no, eran 7 300 soles"]),
    ("Foto de su perro: comenta, pregunta y guarda «se llama Max»",
     [("archivo", [("perro", "perro.png", "image/png")], ""), "se llama Max", "¿cómo se llama mi perro?"]),
    ("La misma foto otra vez: no vuelve a mirarla", [("archivo", [("perro", "perro_otra_vez.png", "image/png")], "")]),
    ("Captura de un chat con un cliente", [("archivo", [("chat", "captura.png", "image/png")], "mira lo que me escribieron")]),
    ("Álbum de 2 fotos: una sola respuesta",
     [("archivo", [("polo", "polo.png", "image/png"), ("zapatillas", "zapatillas.png", "image/png")], "lo nuevo de la tienda")]),
    ("PDF escaneado de 3 páginas y una pregunta después",
     [("archivo", [("pdf", "contrato.pdf", "application/pdf")], ""), "¿cuánto es la renta del local según el contrato?"]),
    ("Nota de voz: se atiende como si la hubiera escrito", [("archivo", [("voz", "nota_de_voz.ogg", "audio/mpeg")], "")]),
    ("Nota de voz en silencio: no inventa", [("archivo", [("silencio", "nota_de_voz2.ogg", "audio/wav")], "")]),
    ("Video: solo se guarda", [("archivo", [("video", "video.mp4", "video/mp4")], ""), "¿qué dice el video?"]),
]


def main() -> int:
    with isolated_instance(ISOLATED_ENV) as url:
        api = Api(url)
        gen = subprocess.run(["docker", "exec", "-i", E2E_NAME, "python", "-"], input=GENERAR,
                             capture_output=True, text=True, timeout=300)
        if gen.returncode != 0:
            raise SystemExit("no se pudieron generar los archivos: " + gen.stderr[-400:])
        media = {k: base64.b64decode(v) for k, v in json.loads(gen.stdout).items()}
        lineas = ["# Muestra: ver y oír (Fase 3)", "",
                  f"Generado el {time.strftime('%Y-%m-%d %H:%M')} en una instancia aislada, con datos inventados "
                  "(fotos dibujadas y voces sintéticas) y OpenRouter real. Debajo de cada respuesta: acción · segundos · costo del comentario."]
        for n, (titulo, turnos) in enumerate(CASOS, 1):
            lineas += ["", f"## {n}. {titulo}", ""]
            for turno in turnos:
                t0 = time.monotonic()
                if isinstance(turno, tuple):
                    _, archivos, leyenda = turno
                    items = []
                    for clave, nombre, mime in archivos:
                        r = api.upload(nombre, media[clave], mime)
                        items.append({"id": r["id"], "filename": nombre, "kind": r["kind"], "duplicate": bool(r.get("duplicate"))})
                    quien = " y ".join(f"«{a[1]}»" for a in archivos)
                    lineas.append(f"**Jefe:** _(envía {quien})_" + (f" {leyenda}" if leyenda else "") + "  ")
                    r = api.ok("POST", "/conversar/archivo", {"archivos": items, "channel": "muestra", "chat_id": CHAT,
                                                              "leyenda": leyenda, "voz": "voz" in archivos[0][1]})
                else:
                    lineas.append(f"**Jefe:** {turno}  ")
                    r = api.ok("POST", "/conversar", {"mensajes": [turno], "channel": "muestra", "chat_id": CHAT})
                costo = (r.get("llm") or {}).get("costo_usd") or 0.0
                lineas.append(f"**Isabelle:** {r['texto'].replace(chr(10), '  ' + chr(10))}  ")
                lineas.append(f"<sub>{r.get('accion') or '—'} · {time.monotonic() - t0:.1f} s · {costo:.5f} USD</sub>")
                lineas.append("")
            print(f"{n:2}. {titulo}: ok", flush=True)
        uso = api.ok("GET", "/usage")
        por = {}
        for d in uso["dias"]:
            for m in d["por_modelo"]:
                k = m["proveedor"]
                por[k] = (por.get(k, (0, 0.0))[0] + m["peticiones"], por.get(k, (0, 0.0))[1] + (m["costo_usd"] or 0))
        lineas += ["", "## Cifras", ""] + [f"- {k}: {n} llamada(s), {c:.5f} USD" for k, (n, c) in sorted(por.items())]
        lineas.append(f"- Total del día en la instancia aislada: {uso['gastado_hoy_usd']:.5f} USD (sin las voces sintéticas).")
        OUT.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        print(f"Escrito {OUT.relative_to(REPO)} · {uso['gastado_hoy_usd']:.5f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
