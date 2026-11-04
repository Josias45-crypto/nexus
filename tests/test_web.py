"""Fase 5 sin red: la API entera exige sesión (o el token interno de los canales), login con espera
creciente, «Lo guardado» que solo descarga y la página sin librerías externas.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/w -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_web
"""

import logging
import os
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from core import auth, db, inbox, iniciativa, memory, ui, web
from core.main import app

CLAVE = "galaxia-azul-2026"


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for r in c.execute("SELECT id FROM events").fetchall():
                memory.delete_chunks(c, r["id"])
            for t in ("events", "medios", "digests", "sesiones", "kv"):
                c.execute(f"DELETE FROM {t}")
        (auth.INTERNO / "web.json").unlink(missing_ok=True)
        auth._fallos.clear()
        self.c = TestClient(app, base_url="http://testserver")
        self.token = {auth.CABECERA: auth.token_interno()}

    def entrar(self) -> None:
        auth.fijar_clave(CLAVE)
        r = self.c.post("/login", data={"clave": CLAVE})
        self.assertEqual(r.status_code, 200)


class SesionTest(Base):
    def test_toda_la_api_exige_sesion(self):
        for metodo, ruta in [("GET", "/health"), ("GET", "/usage"), ("POST", "/conversar"), ("GET", "/brain/graph"),
                             ("GET", "/brain/stream"), ("GET", "/guardado/lista"), ("GET", "/web/panel"),
                             ("POST", "/inbox/text"), ("GET", "/docs"), ("GET", "/openapi.json"), ("GET", "/charla"),
                             ("POST", "/reindex"), ("GET", "/guardado/" + "a" * 32 + "/original")]:
            with self.subTest(ruta=ruta):
                r = self.c.request(metodo, ruta)
                self.assertEqual(r.status_code, 401, ruta)
        r = self.c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        self.assertEqual(self.c.get("/login").status_code, 200)

    def test_token_interno_de_los_canales(self):
        self.assertEqual(self.c.get("/health", headers=self.token).status_code, 200)
        self.assertEqual(self.c.get("/health", headers={auth.CABECERA: "otro"}).status_code, 401)

    def test_login_cookie_y_salida(self):
        self.assertEqual(self.c.post("/login", data={"clave": CLAVE}).status_code, 409, "sin contraseña creada")
        auth.fijar_clave(CLAVE)
        r = self.c.post("/login", data={"clave": CLAVE})
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("samesite=strict", cookie.lower())
        self.assertIn("Path=/", cookie)
        self.assertEqual(self.c.get("/web/panel").status_code, 200)
        with db.connect() as c:
            fila = c.execute("SELECT huella FROM sesiones").fetchone()["huella"]
        self.assertNotIn(fila, cookie, "en la base solo queda la huella del token")
        self.c.post("/logout")
        self.assertEqual(self.c.get("/web/panel").status_code, 401)

    def test_espera_creciente_y_sin_clave_en_registros(self):
        auth.fijar_clave(CLAVE)
        registros: list[str] = []

        class Captura(logging.Handler):
            def emit(self, record):
                registros.append(record.getMessage())

        h = Captura()
        logging.getLogger().addHandler(h)
        try:
            r = self.c.post("/login", data={"clave": "clave-equivocada-1"})
            self.assertEqual(r.status_code, 401)
            r = self.c.post("/login", data={"clave": CLAVE})  # aunque sea la buena: debe esperar
            self.assertEqual(r.status_code, 429)
            self.assertIn("Retry-After", r.headers)
            ip = "testclient"
            self.assertEqual(auth.fallo(ip), 2)
            self.assertEqual(auth.fallo(ip), 4, "la espera crece")
        finally:
            logging.getLogger().removeHandler(h)
        self.assertFalse(any("clave-equivocada" in m or CLAVE in m for m in registros))
        self.assertNotIn(CLAVE, (auth.INTERNO / "web.json").read_text(), "solo el hash")
        self.assertTrue(auth.verificar_clave(CLAVE))
        self.assertFalse(auth.verificar_clave(CLAVE + "x"))

    def test_cabeceras_de_seguridad(self):
        self.entrar()
        r = self.c.get("/")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")


class GuardadoTest(Base):
    def guardar(self, data: bytes, nombre: str, mime: str) -> str:
        return inbox.save(data, nombre, mime, "web")["id"]

    def test_los_originales_solo_se_descargan(self):
        self.entrar()
        for nombre, data, mime in [("pagina.html", b"<script>alert(1)</script>", "text/html"),
                                   ("dibujo.svg", b'<svg onload="alert(1)"/>', "image/svg+xml"),
                                   ("grande.bin", os.urandom(3 * 1024 * 1024), "application/octet-stream")]:
            with self.subTest(nombre=nombre):
                eid = self.guardar(data, nombre, mime)
                r = self.c.get(f"/guardado/{eid}/original")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["content-type"], "application/octet-stream")
                self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
                self.assertIn(nombre, r.headers["content-disposition"])
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                self.assertIn("sandbox", r.headers["content-security-policy"])
                self.assertEqual(r.content, data)

    def test_rutas_relativas_y_fuera_de_raw(self):
        self.entrar()
        for ruta in ("/guardado/..%2F..%2Fetc%2Fpasswd/original", "/guardado/../../etc/passwd",
                     "/guardado/ABC/original", "/guardado/" + "0" * 32 + "/original"):
            with self.subTest(ruta=ruta):
                self.assertIn(self.c.get(ruta).status_code, (404, 422))
        eid = self.guardar(b"nota", "n.txt", "text/plain")
        with db.connect() as c:
            c.execute("UPDATE events SET raw_path = '/etc/passwd' WHERE id = ?", (eid,))
        self.assertEqual(self.c.get(f"/guardado/{eid}/original").status_code, 404, "fuera de data/raw")

    def test_lista_detalle_y_vista(self):
        self.entrar()
        from tests import fixtures

        foto = self.guardar(fixtures.image_with_text(["hola"]), "foto.png", "image/png")
        nota = self.guardar(b"Rosa paga los viernes", "nota.txt", "text/plain")
        d = self.c.get("/guardado/lista").json()
        self.assertEqual(d["total"], 2)
        self.assertEqual(self.c.get("/guardado/lista?tipo=fotos").json()["items"][0]["id"], foto)
        self.assertEqual(self.c.get("/guardado/lista?tipo=nada").status_code, 422)
        det = self.c.get(f"/guardado/{nota}").json()
        self.assertNotIn("raw_path", det)
        r = self.c.get(f"/guardado/{foto}/vista")
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"), "re-codificada como JPEG")
        self.assertEqual(self.c.get(f"/guardado/{nota}/vista").status_code, 404)


class WebTest(Base):
    def test_la_web_es_la_misma_conversacion_de_telegram(self):
        with db.connect() as c:
            iniciativa._set(c, iniciativa.K_DESTINO, {"channel": "telegram", "chat_id": "1001"})
        self.assertEqual(web.conversacion("web", "web"), ("telegram", "1001"))
        self.assertEqual(web.conversacion("telegram", "7"), ("telegram", "7"))
        with db.connect() as c:
            c.execute("DELETE FROM kv")
        with mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_IDS": "555, 777"}):
            self.assertEqual(web.conversacion("web", "web"), ("telegram", "555"))

    def test_panel_esencial(self):
        self.entrar()
        inbox.save(b"una nota", "n.txt", "text/plain", "web")
        p = self.c.get("/web/panel").json()
        self.assertEqual(set(p) - {"desde"}, {"dias_de_vida", "elementos", "gasto_hoy_usd"})
        self.assertEqual((p["dias_de_vida"], p["elementos"]), (1, 1))

    def test_pagina_sin_librerias_y_con_cuidado_de_la_bateria(self):
        for pagina in (ui.PAGE, ui.GUARDADO, ui.login(True)):
            self.assertFalse(re.search(r"(src|href)=[\"']https?://", pagina), "sin CDN ni recursos externos")
            self.assertNotIn("<iframe", pagina)
        for pieza in ("/brain/graph", "/brain/stream", "visibilitychange", "prefers-reduced-motion", "requestAnimationFrame",
                      "FPS", "devicePixelRatio", "dragover", "/conversar/archivo", "/guardado"):
            self.assertIn(pieza, ui.PAGE)
        self.assertNotIn("racha", ui.PAGE.lower())


if __name__ == "__main__":
    unittest.main()
