"""Prueba de extremo a extremo de NEXUS (solo librería estándar).

Uso:
    python3 tests/e2e.py [--url http://localhost:8000]
                         [--audio ruta.wav --audio-phrase "frase"] [--chaos]

Sin --url levanta una instancia temporal aislada (contenedor nexus-e2e, puerto 8001) con la
misma imagen, pero con datos y respaldos en una carpeta temporal que se borra al terminar: la
memoria real no se toca. Requiere la imagen construida (`docker compose build core`).
Con NEXUS_E2E_IMAGE=<imagen> usa esa imagen (p. ej. `docker build -t nexus-core:prueba .`).
Con --url prueba contra esa instancia (y deja en ella los datos sintéticos).

Usa solo datos sintéticos marcados con un id de corrida único. El chat y los embeddings usan
OpenRouter de verdad (la clave de .env; cuesta centavos). Ver y oír (caso 23): una sola llamada real
de visión; el audio va por el mismo camino del bot con la transcripción simulada (--audio sí
transcribe de verdad). --chaos levanta una segunda instancia aislada sin salida a OpenRouter (un
proxy que no responde): el original se guarda, el procesamiento espera y la búsqueda cae a solo
palabras.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests import fixtures  # noqa: E402
NO_INFO = "No tengo información sobre eso en mi memoria."
GROWTH_FIELDS = [
    "dias_de_vida",
    "nacio",
    "aprendido",
    "hoy",
    "pendientes_de_digerir",
    "fallidos",
    "ultimos_dias",
]
LEARNED_FIELDS = ["elementos", "por_tipo", "recuerdos", "palabras_aprox", "conceptos"]

# Tiempos generosos: en CPU el worker, los embeddings y el LLM son lentos
PROCESS_TIMEOUT = 180
DIGEST_TIMEOUT = 900
AUDIO_TIMEOUT = 900
HTTP_TIMEOUT = 600


class Skip(Exception):
    pass


class Fail(Exception):
    pass


def token_interno() -> str:
    """El token de la API (X-Nexus-Token): el de la instancia aislada, o el de la real en data/."""
    if t := os.getenv("NEXUS_INTERNAL_TOKEN", "").strip():
        return t
    try:
        return (REPO / "data" / "interno" / "token").read_text().strip()
    except OSError:
        return ""


# La instancia aislada usa su propio token (la API entera exige sesión o este token)
TOKEN_PRUEBA = uuid.uuid4().hex + uuid.uuid4().hex


class Api:
    def __init__(self, base: str, token: str | None = None):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body=None, headers=None, raw=False, token: bool = True):
        data = None
        headers = dict(headers or {})
        if token:
            # Instancias aisladas (8001 e2e, 8002 caos): su token; la real: el de data/interno/token
            aislada = self.base.rsplit(":", 1)[-1] in ("8001", "8002")
            headers.setdefault("X-Nexus-Token", self.token or (TOKEN_PRUEBA if aislada else token_interno()))
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(body, bytes):
            data = body
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        if raw:
            return status, payload
        try:
            return status, json.loads(payload or b"null")
        except json.JSONDecodeError:
            return status, payload.decode(errors="replace")

    def ok(self, method: str, path: str, body=None, headers=None):
        status, data = self.call(method, path, body, headers)
        if status != 200:
            raise Fail(f"{method} {path} -> HTTP {status}: {str(data)[:200]}")
        return data

    def upload(self, filename: str, content: bytes, mime: str):
        boundary = "nexus-e2e-" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.ok(
            "POST",
            "/inbox/file?source=e2e",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


# ---------- utilidades ----------


def event_id(resp: dict) -> str:
    for key in ("id", "event_id"):
        if isinstance(resp, dict) and resp.get(key):
            return resp[key]
    raise Fail(f"La bandeja no devolvió id del evento: {resp}")


def wait_status(api: Api, eid: str, timeout: int, done=("processed",)) -> dict:
    """Espera a que el worker deje el evento en un estado final."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        for ev in api.ok("GET", "/inbox?limit=500"):
            if ev["id"] == eid:
                last = ev
                break
        if last and last["status"] in done:
            return last
        if last and last["status"] in ("failed", "unsupported", "empty"):
            raise Fail(f"evento en '{last['status']}': {last.get('error')}")
        time.sleep(5)
    estado = last["status"] if last else "no encontrado"
    raise Fail(f"tras {timeout} s el evento sigue en '{estado}'")


def wait_final(api: Api, eid: str, timeout: int) -> dict:
    """Espera cualquier estado final (procesado, sin texto, no soportado o fallido)."""
    return wait_status(api, eid, timeout, done=("processed", "empty", "unsupported", "failed"))


def check_answer(resp: dict, must_contain: str | None = None) -> str:
    answer = (resp.get("answer") or "").strip()
    sources = resp.get("sources") or []
    if not answer:
        raise Fail("respuesta vacía")
    if re.fullmatch(r"(\[\d+\][\s,.]*)+", answer):
        raise Fail(f"la respuesta solo cita fuentes: {answer!r}")
    if answer.startswith(NO_INFO[:20]):
        raise Fail(f"no encontró el recuerdo: {answer[:120]!r}")
    if not sources:
        raise Fail("respuesta sin sources")
    if must_contain and not any(must_contain in (s.get("snippet") or "") for s in sources):
        raise Fail(f"ninguna fuente contiene {must_contain!r}")
    return answer


def make_pdf(text: str) -> bytes:
    """PDF mínimo válido de una página con una línea de texto (Helvetica)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


E2E_NAME = "nexus-e2e"
E2E_PORT = 8001


def _docker(*args: str) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"docker {args[0]}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _imagen() -> str:
    """La imagen de nexus-core, o NEXUS_E2E_IMAGE para probar una construida aparte (así no se
    reconstruye la imagen de los contenedores reales)."""
    return os.environ.get("NEXUS_E2E_IMAGE") or _docker("inspect", "nexus-core", "-f", "{{.Config.Image}}")


@contextmanager
def isolated_instance(extra_env: dict | None = None, image: str | None = None,
                      name: str | None = None, port: int | None = None):
    """Contenedor temporal con datos propios y la imagen de la instalación en marcha (o la
    indicada). Ya no necesita Ollama: chat, visión, audio y embeddings van por OpenRouter."""
    try:
        image = image or _imagen()
    except RuntimeError as e:
        raise SystemExit(f"Construye la imagen antes (docker compose build core): {e}")
    name, port = name or E2E_NAME, port or E2E_PORT

    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    work = Path(tempfile.mkdtemp(prefix="nexus-e2e-"))
    (work / "data").mkdir()
    (work / "backups").mkdir()
    cmd = [
        "run", "-d", "--rm", "--name", name,
        "-p", f"127.0.0.1:{port}:8000",
    ]
    # Primero el .env y después los -e: las variables de la prueba mandan
    if (REPO / ".env").exists():
        cmd += ["--env-file", str(REPO / ".env")]
    cmd += [
        "-v", f"{work / 'data'}:/data", "-v", f"{work / 'backups'}:/backups",
        "-e", "NEXUS_DATA_DIR=/data", "-e", "NEXUS_BACKUP_DIR=/backups",
        "-e", "NEXUS_BACKUP_HOURS=0", "-e", f"NEXUS_INTERNAL_TOKEN={TOKEN_PRUEBA}",
    ]
    for key, value in (extra_env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    try:
        _docker(*cmd, image)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 90
        while True:
            try:
                req = urllib.request.Request(url + "/health", headers={"X-Nexus-Token": TOKEN_PRUEBA})
                with urllib.request.urlopen(req, timeout=5):
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.monotonic() > deadline:
                    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                    raise SystemExit("La instancia de prueba no arrancó:\n" + logs.stderr[-1500:])
                time.sleep(2)
        yield url
    finally:
        subprocess.run(["docker", "stop", "-t", "15", name], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
        if work.exists():
            print(f"Aviso: no se pudo borrar {work} (permisos); bórralo a mano.")


# ---------- pruebas ----------


class Suite:
    def __init__(self, api: Api, args):
        self.api = api
        self.args = args
        self.rid = time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6]
        self.tag = f"e2e{self.rid}"
        self.short_text = (
            f"Dato sintético de prueba {self.tag}: el planeta ficticio Zorvak{self.rid} "
            f"tiene exactamente tres lunas, llamadas Ilma, Prit y Duvesa."
        )

    def t1_health(self):
        d = self.api.ok("GET", "/health")
        if d.get("status") != "ok":
            raise Fail(f"status={d.get('status')}")
        e = d["embeddings"]
        return f"modelo {d.get('llm_model')}, embeddings {e['modelo']} ({e['dimension']}), vectores {e['con_vector']}/{e['trozos']}"

    def t2_text(self):
        resp = self.api.ok("POST", "/inbox/text", {"text": self.short_text, "source": "e2e"})
        if resp.get("duplicate"):
            raise Fail("el texto nuevo se marcó como duplicado")
        eid = event_id(resp)
        wait_status(self.api, eid, PROCESS_TIMEOUT)
        q = urllib.parse.quote(f"Zorvak{self.rid} lunas")
        hits = self.api.ok("GET", f"/search?q={q}&k=5")
        if not any(h.get("event_id") == eid for h in hits):
            raise Fail("/search no devuelve el texto guardado")
        ans = check_answer(
            self.api.ok("POST", "/ask", {"question": f"¿Cuántas lunas tiene el planeta Zorvak{self.rid}?"}),
            must_contain=self.rid,
        )
        return f"respuesta: {ans[:60]!r}"

    def t3_duplicate(self):
        resp = self.api.ok("POST", "/inbox/text", {"text": self.short_text, "source": "e2e"})
        if resp.get("duplicate") is not True:
            raise Fail(f"duplicate={resp.get('duplicate')!r}")
        return "duplicate=true"

    def t4_long_txt(self):
        name = f"Melivora{self.rid}"
        paragraphs = [
            f"Informe sintético {self.tag} sobre la cooperativa apícola ficticia {name}.",
            f"La cooperativa {name} se fundó en un valle imaginario y reúne a cuarenta familias "
            "que crían abejas meliponas sin aguijón. Su producto principal es una miel ácida "
            "de color ámbar que se cosecha dos veces al año, al final de cada temporada de lluvias.",
            "Para proteger las colmenas, los socios plantan setos de flores nativas alrededor de "
            "los apiarios y evitan cualquier pesticida en un radio de tres kilómetros. Cada familia "
            "lleva un cuaderno con el peso de las colmenas y la fecha de floración de cada especie.",
            "El mayor problema de la cooperativa es el transporte: el camino al mercado se corta "
            "con frecuencia y la miel debe guardarse en tinajas de barro hasta que se pueda vender. "
            "Por eso decidieron construir un almacén comunitario con control de humedad.",
            "Además de la miel, venden propóleo, cera para velas y polen deshidratado. Las ganancias "
            "se reparten según las horas de trabajo y un diez por ciento se reserva para capacitar "
            "a los jóvenes en el cuidado de las abejas y en contabilidad básica.",
            "Cada año organizan una feria en la plaza del pueblo donde enseñan a los visitantes "
            "a distinguir las especies de abejas nativas, preparan dulces con miel y explican por "
            "qué las abejas sin aguijón son importantes para polinizar los cultivos de la zona.",
            f"En resumen, {name} es un ejemplo de apicultura sostenible basada en abejas nativas, "
            "cooperación entre familias y venta de varios productos de la colmena.",
        ]
        text = "\n\n".join(paragraphs)
        if len(text) <= 1500:
            raise Fail(f"texto de prueba demasiado corto ({len(text)})")
        eid = event_id(self.api.upload(f"{self.tag}-informe.txt", text.encode(), "text/plain"))
        wait_status(self.api, eid, PROCESS_TIMEOUT)

        deadline = time.monotonic() + DIGEST_TIMEOUT
        digest = None
        while time.monotonic() < deadline and not digest:
            for r in self.api.ok("GET", "/knowledge?limit=200").get("resumenes", []):
                if r.get("event_id") == eid and r.get("status", "done") == "done":
                    digest = r
            if not digest:
                time.sleep(10)
        if not digest:
            raise Fail(f"sin digest 'done' tras {DIGEST_TIMEOUT} s")
        if not (digest.get("summary") or "").strip():
            raise Fail("digest sin resumen")
        if not digest.get("concepts"):
            raise Fail("digest sin conceptos")

        check_answer(
            self.api.ok("POST", "/ask", {"question": f"¿De qué trata el informe sobre {name}?"})
        )
        return f"{len(text)} car., {len(digest['concepts'])} conceptos"

    def t5_pdf(self):
        phrase = f"El faro sintetico Brumaleon{self.rid} se enciende con luz violeta"
        eid = event_id(self.api.upload(f"{self.tag}.pdf", make_pdf(phrase), "application/pdf"))
        wait_status(self.api, eid, PROCESS_TIMEOUT)
        check_answer(
            self.api.ok("POST", "/ask", {"question": f"¿De qué color es la luz del faro Brumaleon{self.rid}?"}),
            must_contain=self.rid,
        )
        return "frase encontrada por /ask"

    def t6_audio(self):
        if not self.args.audio:
            raise Skip("sin --audio")
        path = Path(self.args.audio)
        if not path.is_file():
            raise Fail(f"no existe {path}")
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}
        resp = self.api.upload(path.name, path.read_bytes(), mime.get(path.suffix.lower(), "audio/wav"))
        eid = event_id(resp)
        wait_status(self.api, eid, AUDIO_TIMEOUT)
        if not self.args.audio_phrase:
            return "transcrito (sin --audio-phrase que verificar)"
        q = urllib.parse.quote(self.args.audio_phrase)
        hits = self.api.ok("GET", f"/search?q={q}&k=5")
        if not any(h.get("event_id") == eid for h in hits):
            raise Fail("/search no encuentra la frase en la transcripción")
        return "frase encontrada" + (" (audio ya existía)" if resp.get("duplicate") else "")

    def t7_unrelated(self):
        q = f"¿Cuál es la receta secreta del pastel cuántico Xyloquar{self.rid}?"
        ans = (self.api.ok("POST", "/ask", {"question": q}).get("answer") or "").strip()
        if ans != NO_INFO:
            raise Fail(f"respuesta inesperada: {ans[:100]!r}")
        return "respondió que no sabe"

    def t8_pages(self):
        g = self.api.ok("GET", "/growth")
        missing = [f for f in GROWTH_FIELDS if f not in g]
        missing += [f"aprendido.{f}" for f in LEARNED_FIELDS if f not in g.get("aprendido", {})]
        if missing:
            raise Fail(f"faltan campos en /growth: {missing}")
        for path in ("/dashboard", "/"):
            status, _ = self.api.call("GET", path, raw=True)
            if status != 200:
                raise Fail(f"GET {path} -> HTTP {status}")
        notes = []
        for path in ("/brain", "/brain/graph"):
            status, _ = self.api.call("GET", path, raw=True)
            if status == 404:
                notes.append(f"{path} SKIP (404)")
            elif status != 200:
                raise Fail(f"GET {path} -> HTTP {status}")
            else:
                notes.append(f"{path} ok")
        return "growth, dashboard y / ok; " + ", ".join(notes)

    def t9_backup(self):
        current = sum(self.api.ok("GET", "/worker").get("eventos_por_estado", {}).values())
        b = self.api.ok("POST", "/backup")
        if "eventos_en_la_copia" not in b:
            raise Fail(f"POST /backup sin eventos_en_la_copia: {list(b) if isinstance(b, dict) else b}")
        if b["eventos_en_la_copia"] < current - 1:
            raise Fail(f"la copia tiene {b['eventos_en_la_copia']} eventos; hay {current}")
        self.api.ok("POST", "/requeue")
        return f"copia con {b['eventos_en_la_copia']} de {current} eventos; requeue ok"

    def t10_chaos(self):
        """OpenRouter inalcanzable (un proxy que no responde) en una segunda instancia aislada: el
        original se guarda intacto, el procesamiento (embeddings) queda en espera con pausa creciente
        y sin gastar intentos, /ask cae a la búsqueda por palabras y lo dice, y el bot responde una
        línea clara."""
        if not self.args.chaos:
            raise Skip("sin --chaos")
        text = f"Dato sintético de caos {self.tag}: el río ficticio Ondarel{self.rid} fluye hacia el norte."
        with isolated_instance(CHAOS_ENV, name=CHAOS_NAME, port=CHAOS_PORT) as url:
            api = Api(url)
            eid = event_id(api.ok("POST", "/inbox/text", {"text": text, "source": "e2e"}))
            raw = _docker("exec", CHAOS_NAME, "python", "-c", (
                "import hashlib, sqlite3; c = sqlite3.connect('/data/nexus.db');"
                f"p, h = c.execute(\"SELECT raw_path, sha256 FROM events WHERE id = '{eid}'\").fetchone();"
                "print(hashlib.sha256(open(p, 'rb').read()).hexdigest() == h)"
            ))
            if raw != "True":
                raise Fail("el original no quedó guardado intacto")
            first = self._chaos_espera(eid, 1)
            r = api.ok("POST", "/ask", {"question": f"¿Hacia dónde fluye el río Ondarel{self.rid}?"})
            if "solo pude buscar por palabras" not in r["answer"]:
                raise Fail(f"/ask no dijo que buscó solo por palabras: {r.get('answer')!r}")
            bot = subprocess.run(
                ["docker", "exec", CHAOS_NAME, "python", "-m", "channels.simulado",
                 "--url", "http://localhost:8000", "--tag", self.rid, "--step", "caos"],
                capture_output=True, text=True, timeout=300,
            )
            if bot.returncode != 0:
                raise Fail("bot: " + bot.stdout[-300:])
            second = self._chaos_espera(eid, 2)  # tras la pausa vuelve a intentar
        return (f"original intacto; procesamiento en espera {second['veces']} veces sin gastar intentos "
                f"{second['fila']}; pausa {first['pausa']} s -> {second['pausa']} s; /ask por palabras")

    def _chaos_espera(self, eid: str, times: int) -> dict:
        """Espera a que el procesamiento de eid haya quedado en espera `times` veces (cada vuelta es
        un reintento tras la pausa) sin gastar intentos ni quedar 'failed'."""
        deadline = time.monotonic() + 300
        mark = f"Procesar {eid} queda en espera"
        n, row = 0, ""
        while time.monotonic() < deadline:
            logs = subprocess.run(["docker", "logs", CHAOS_NAME], capture_output=True, text=True)
            n = (logs.stdout + logs.stderr).count(mark)
            row = _docker("exec", CHAOS_NAME, "python", "-c", (
                "import sqlite3; c = sqlite3.connect('/data/nexus.db');"
                f"print(c.execute(\"SELECT status, attempts FROM events WHERE id = '{eid}'\").fetchone())"
            ))
            if row != "('pending', 0)":
                raise Fail(f"el evento no quedó en espera sin gastar intentos: {row}")
            if n >= times:
                w = Api(f"http://127.0.0.1:{CHAOS_PORT}").ok("GET", "/worker")["llm"]
                return {"veces": n, "pausa": w["pausa_s"], "fila": row}
            time.sleep(3)
        raise Fail(f"el procesamiento no se reintentó (en espera {n} vez/veces, fila {row})")

    def t11_channel(self):
        if not self.isolated:
            raise Skip("solo en instancia aislada (necesita el programador acelerado)")
        r = subprocess.run(
            ["docker", "exec", E2E_NAME, "python", "-m", "channels.simulado",
             "--url", "http://localhost:8000", "--tag", self.rid],
            capture_output=True, text=True, timeout=600,
        )
        fails = [l for l in r.stdout.splitlines() if l.startswith("FALLA")]
        if r.returncode != 0:
            raise Fail("; ".join(fails)[:300] or (r.stderr or r.stdout)[-300:])
        oks = sum(1 for l in r.stdout.splitlines() if l.startswith("ok "))
        return f"{oks} comprobaciones: charla sin comandos, guardar, botones, recordatorio e insistencia"

    def _sim_step(self, name: str) -> str:
        r = subprocess.run(
            ["docker", "exec", E2E_NAME, "python", "-m", "channels.simulado",
             "--url", "http://localhost:8000", "--tag", self.rid, "--step", name],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            fails = [l for l in r.stdout.splitlines() if l.startswith("FALLA")]
            raise Fail(f"{name}: " + ("; ".join(fails) or (r.stderr or r.stdout)[-300:])[:300])
        return r.stdout

    def t18_reminder_restart(self):
        if not self.isolated:
            raise Skip("solo en instancia aislada (reinicia el contenedor)")
        self._sim_step("pedir")
        # El bot simulado vive en este contenedor: reiniciarlo borra toda memoria del proceso
        _docker("restart", "-t", "10", E2E_NAME)
        deadline = time.monotonic() + 90
        while True:
            try:
                self.api.ok("GET", "/health")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise Fail("la instancia no volvió tras el reinicio")
                time.sleep(2)
        self._sim_step("confirmar")
        rows = self.api.ok("GET", "/reminders?" + urllib.parse.urlencode({"chat_id": "1001"}))
        mine = [r for r in rows if f"Brontal{self.rid}" in r["text"]]
        if len(mine) != 1:
            raise Fail(f"se esperaba 1 fila y hay {len(mine)}")
        self._sim_step("entregar")
        return f"guardar → «cancela el n.º {mine[0]['id']}» → reinicio → «no» → aviso por /outbox → «ya lo hice»"

    def t19_memoria(self):
        """Conversación sintética por /conversar: el historial se busca y la ficha del jefe aprende
        solo de lo que dice él (el worker la actualiza cuando nadie espera respuesta)."""
        chat = f"e2e{self.rid}"
        for texto in (f"Te cuento: mi proveedor de laptops es Zarvel{self.rid}, me trae 10 cada lunes",
                      "y mi meta este año es abrir un segundo local"):
            r = self.api.ok("POST", "/conversar", {"mensajes": [texto], "channel": "e2e", "chat_id": chat})
            if not r.get("texto") or r["texto"].startswith("/"):
                raise Fail(f"respuesta vacía o con comando: {r}")
        hits = self.api.ok("GET", "/charla?" + urllib.parse.urlencode({"q": f"Zarvel{self.rid}"}))
        if not any(f"Zarvel{self.rid}" in h["texto"] for h in hits):
            raise Fail("el historial no encuentra el mensaje por texto")
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            mem = self.api.ok("GET", "/charla/memoria")
            # El modelo a veces anota el nombre sin el sufijo de la corrida: basta «Zarvel»
            if "zarvel" in json.dumps(mem["ficha"], ensure_ascii=False).lower() and mem["resumen"]:
                return f"historial con búsqueda; ficha: {mem['ficha']['personas'][:1]}; resumen {len(mem['resumen'].split())} palabras"
            time.sleep(5)
        raise Fail(f"la ficha no aprendió el dato en 4 min: {mem}")

    def t20_recordatorio_90s(self):
        """El camino del bot real: «recuérdame en 90 segundos…» por el canal simulado, el aviso
        aparece en /outbox con «Ya lo hice» y el botón lo cierra."""
        if not self.isolated:
            raise Skip("solo en instancia aislada")
        out = self._sim_step("boton90")
        return f"{out.count('ok ')} comprobaciones: creado → aviso con botones en /outbox → «Ya lo hice»"

    def t21_saludo(self):
        """Iniciativa por el camino del bot real: el jefe ajusta el saludo por el canal simulado, el
        programador lo redacta (OpenRouter real) y lo deja en /outbox, el bot lo entrega y «ok ok» cierra."""
        if not self.isolated:
            raise Skip("solo en instancia aislada")
        self._sim_step("saludo_ajuste")
        # Como si hubiera escrito hace 2 h y pidió el saludo de hoy hace un minuto (sin esperar a las 7:30)
        prep = subprocess.run(
            ["docker", "exec", E2E_NAME, "python", "-c",
             "from datetime import timedelta\n"
             "from core import clock, db, iniciativa\n"
             "now = clock.now_local()\n"
             "tipo = 'manana' if now.hour < 11 else 'noche'\n"
             "with db.connect() as c:\n"
             "    c.execute('UPDATE charla SET created_at = ?', ((clock.now_utc() - timedelta(hours=2)).isoformat(),))\n"
             "    c.execute('DELETE FROM iniciativa WHERE dia = ?', (now.date().isoformat(),))\n"
             "    iniciativa._set(c, f'iniciativa:hora:{now.date().isoformat()}:{tipo}', (now - timedelta(minutes=1)).strftime('%H:%M'))\n"
             "print(tipo)"],
            capture_output=True, text=True, timeout=60,
        )
        tipo = prep.stdout.strip()
        if prep.returncode != 0 or tipo not in ("manana", "noche"):
            raise Fail("preparación: " + (prep.stderr or prep.stdout)[-300:])
        out = self._sim_step("saludo")
        fila = next(s for s in self.api.ok("GET", "/iniciativa")["saludos"] if s["estado"] == "enviado")
        return f"{out.count('ok ')} comprobaciones: saludo de {tipo} ({fila['fuente']}{', ' + fila['detalle'] if fila['detalle'] else ''}) por /outbox → «ok ok» sin pregunta"

    def t22_internet_olvido(self):
        """Por el camino del bot real: búsqueda en internet con aviso (1 búsqueda real), guardar «sí»
        y «olvida lo de X» con confirmación por texto y borrado verificado en /search."""
        if not self.isolated:
            raise Skip("solo en instancia aislada")
        out = self._sim_step("internet_olvido")
        uso = self.api.ok("GET", "/usage")
        if uso.get("busquedas_internet_hoy", 0) < 1:
            raise Fail(f"/usage no cuenta la búsqueda: {uso.get('busquedas_internet_hoy')}")
        return f"{out.count('ok ')} comprobaciones; /usage: {uso['busquedas_internet_hoy']} búsqueda(s) hoy"

    def t23_ver_oir(self):
        """Fase 3 por el camino del bot real: una foto con una llamada REAL de visión (comenta y pide
        confirmar la cifra), el repetido sin IA, la pregunta desde lo guardado y el video solo
        guardado. Luego una nota de voz por el mismo camino (canal -> API -> conversa -> medios) con
        la transcripción simulada: se atiende como si él la hubiera escrito."""
        if not self.isolated:
            raise Skip("solo en instancia aislada")
        out = self._sim_step("ver")
        r = subprocess.run(["docker", "exec", "-i", E2E_NAME, "python", "-", self.rid], input=OIR_SIMULADO,
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or "FALLA" in r.stdout:
            raise Fail("oír: " + ((r.stdout + r.stderr)[-300:]))
        return f"{out.count('ok ')} comprobaciones de ver; oír: {r.stdout.strip().splitlines()[-1]}"

    def t24_web(self):
        """Fase 5 por HTTP de verdad: sin sesión todo es 401 (y la página redirige a /login), la
        contraseña se crea por la terminal del contenedor, login con cookie HttpOnly y SameSite,
        espera tras fallar, y «Lo guardado» descarga un HTML subido sin ejecutarlo."""
        if not self.isolated:
            raise Skip("solo en instancia aislada (crea una contraseña)")
        sin = Api(self.api.base)
        for ruta in ("/health", "/web/panel", "/guardado/lista", "/brain/graph", "/openapi.json"):
            status, _ = sin.call("GET", ruta, token=False)
            if status != 401:
                raise Fail(f"{ruta} sin sesión -> HTTP {status}")
        clave = "prueba-" + self.rid
        r = subprocess.run(["docker", "exec", "-i", E2E_NAME, "python", "-c",
                            "import sys; from core import auth; auth.fijar_clave(sys.stdin.read().strip())"],
                           input=clave, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise Fail("no se pudo crear la contraseña: " + r.stderr[-200:])
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        status, _ = sin.call("POST", "/login", urllib.parse.urlencode({"clave": "mala"}).encode(), form, token=False)
        status2, _ = sin.call("POST", "/login", urllib.parse.urlencode({"clave": clave}).encode(), form, token=False)
        if (status, status2) != (401, 429):
            raise Fail(f"fallo y espera: {status}, {status2}")
        time.sleep(1.2)
        req = urllib.request.Request(self.api.base + "/login", data=urllib.parse.urlencode({"clave": clave}).encode(),
                                     headers=form, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            cookie = resp.headers.get("Set-Cookie", "")
        if "HttpOnly" not in cookie or "samesite=strict" not in cookie.lower():
            raise Fail(f"cookie sin HttpOnly/SameSite: {cookie[:80]!r}")
        sesion = {"Cookie": cookie.split(";")[0]}
        status, panel = sin.call("GET", "/web/panel", headers=sesion, token=False)
        status_html, pagina = sin.call("GET", "/", headers={**sesion, "Accept": "text/html"}, raw=True, token=False)
        if status != 200 or status_html != 200 or b"/brain/stream" not in pagina:
            raise Fail(f"con sesión: panel {status}, página {status_html}")
        eid = event_id(self.api.upload(f"pagina-{self.rid}.html", b"<script>alert(1)</script>", "text/html"))
        req = urllib.request.Request(f"{self.api.base}/guardado/{eid}/original", headers=sesion)
        with urllib.request.urlopen(req, timeout=30) as resp:
            tipo, disp, cuerpo = resp.headers.get("Content-Type"), resp.headers.get("Content-Disposition", ""), resp.read()
        if tipo != "application/octet-stream" or not disp.startswith("attachment") or cuerpo != b"<script>alert(1)</script>":
            raise Fail(f"descarga: {tipo}, {disp[:40]}")
        return f"5 rutas en 401 sin sesión; 401 → 429 al fallar; cookie HttpOnly+SameSite; panel {panel['elementos']} elementos; HTML como descarga"

    def t12_unit(self):
        local = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", "tests.test_when"],
            cwd=REPO, capture_output=True, text=True,
        )
        if local.returncode != 0:
            raise Fail("test_when: " + local.stderr[-300:])
        image = _imagen()
        r = subprocess.run(
            ["docker", "run", "--rm", "-e", "NEXUS_DATA_DIR=/tmp",
             "-v", f"{REPO / 'tests'}:/app/tests:ro", image,
             "python", "-m", "unittest", "-q", "tests.test_telegram", "tests.test_whatsapp",
             "tests.test_openrouter", "tests.test_questions", "tests.test_chunker", "tests.test_senses",
             "tests.test_verify", "tests.test_reminders_flow", "tests.test_conversa", "tests.test_charla",
             "tests.test_filtro", "tests.test_recordatorios", "tests.test_persona", "tests.test_iniciativa", "tests.test_busqueda_olvido",
             "tests.test_medios", "tests.test_embeddings", "tests.test_web"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise Fail("canales: " + r.stderr[-300:])
        return "fechas, troceado, citas, preguntas, formatos, Telegram, WhatsApp, OpenRouter, conversación, memoria, filtro y ver/oír"

    def t13_profile(self):
        if not self.isolated:
            raise Skip("solo en instancia aislada")
        code = (
            "import asyncio; from core import profile, briefing;"
            "p = profile.get(); print(p['id']); print(profile.system_prompt());"
            "print(asyncio.run(briefing.build())['texto'])"
        )
        outs = {}
        for name in ("general", "ventas"):
            r = subprocess.run(
                ["docker", "exec", "-e", f"NEXUS_PROFILE={name}", E2E_NAME, "python", "-c", code],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode != 0:
                raise Fail(f"perfil {name}: {r.stderr[-200:]}")
            outs[name] = r.stdout
        if "Isabelle" not in outs["ventas"] or "negocio de ventas" not in outs["ventas"] \
                or "negocio de ventas" in outs["general"]:
            raise Fail("el perfil no cambió el rubro de Isabelle")
        return "general y ventas cambian el rubro de Isabelle y el resumen sin tocar el código"

    def t14_openrouter(self):
        h = self.api.ok("GET", "/health")["llm"]
        if not h["configurado"]:
            raise Fail("OpenRouter sin clave (OPENROUTER_API_KEYS)")
        if h["politica_datos"] != {"zdr": True, "data_collection": "deny"}:
            raise Fail(f"política de datos inesperada: {h['politica_datos']}")
        if h["ultimo"].get("ok") is not True:
            raise Fail(f"la última llamada no respondió: {h['ultimo']}")
        u = self.api.ok("GET", "/usage?days=1")
        hoy = (u.get("dias") or [{}])[0]
        if not hoy.get("peticiones") or not hoy.get("costo_usd"):
            raise Fail(f"/usage no registró peticiones con costo: {hoy}")
        return (f"{h['modelo']} con zdr y data_collection=deny; /usage {hoy['peticiones']} peticiones,"
                f" {hoy['tokens_entrada']}+{hoy['tokens_salida']} tokens, {hoy['costo_usd']:.6f} USD")

    def t15_knowledge(self):
        md = (
            f"# Inventario ficticio {self.tag}\n\nEl almacén Quirmo{self.rid} guarda 48 cajas de clavos.\n\n"
            f"## Proveedores\n\nEl proveedor Daltrex{self.rid} entrega tornillos cada jueves por la mañana."
        )
        resp = self.api.upload(f"{self.tag}-inventario.md", md.encode(), "text/markdown")
        eid = event_id(resp)
        wait_status(self.api, eid, PROCESS_TIMEOUT)
        r = self.api.ok("POST", "/ask", {"question": f"¿Qué día entrega tornillos el proveedor Daltrex{self.rid}?"})
        citas = [s.get("cita") or "" for s in r.get("sources", [])]
        if not any("§ Proveedores" in c for c in citas):
            raise Fail(f"la cita no indica la sección: {citas} · {r.get('answer', '')[:80]}")
        t = self.api.ok("POST", "/ask", {"question": "¿Qué aprendí hoy?"})
        if t.get("tipo") != "temporal" or f"{self.tag}-inventario.md" not in t.get("answer", ""):
            raise Fail(f"'qué aprendí hoy' no listó el documento: {t.get('tipo')} {t.get('answer', '')[:120]}")
        last = self.api.ok("POST", "/ask", {"question": "Resume lo último que subí"})
        if last.get("tipo") != "ultimo":
            raise Fail(f"'lo último que subí' no se reconoció: {last.get('tipo')}")
        rx = self.api.ok("POST", f"/reindex?event_id={eid}")
        if rx.get("reencolados") != 1 or not rx.get("trozos_borrados"):
            raise Fail(f"/reindex no borró lo derivado: {rx}")
        wait_status(self.api, eid, PROCESS_TIMEOUT)
        q = urllib.parse.quote(f"Daltrex{self.rid}")
        if not any(h.get("event_id") == eid for h in self.api.ok("GET", f"/search?q={q}&k=5")):
            raise Fail("tras /reindex el documento no vuelve a encontrarse")
        return "cita con sección, 'qué aprendí hoy', 'lo último', /reindex y vuelta a encontrar"

    def t16_formats(self):
        r, tag = self.rid, self.tag
        docs = {
            # nombre: (contenido, mime, palabra a buscar, dato de origen esperado en el trozo)
            f"{tag}.docx": (fixtures.docx("Garantias", [f"La silla Ergomar{r} tiene garantia de doce meses."]),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            f"Ergomar{r}", "seccion"),
            f"{tag}.xlsx": (fixtures.xlsx({"Ventas": [["Cliente", "Producto"], [f"Velbra{r}", "mesa de roble"]]}),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            f"Velbra{r}", "hoja"),
            f"{tag}.pptx": (fixtures.pptx([("Plan", f"Abrir la tienda Truncal{r} en abril")]),
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            f"Truncal{r}", "diapositiva"),
            f"{tag}.epub": (fixtures.epub([("Uno", f"El faro Ostrel{r} guia a los barcos")]),
                            "application/epub+zip", f"Ostrel{r}", "capitulo"),
            f"{tag}.eml": (fixtures.eml("Envio", f"El proveedor Cardel{r} confirma el envio del lunes."),
                           "message/rfc822", f"Cardel{r}", None),
            f"{tag}.zip": (fixtures.zip_of({"notas/n.md": f"# Nota\n\nLa bodega Fresnal{r} abre a las ocho."}),
                           "application/zip", f"Fresnal{r}", "archivo"),
            f"{tag}.html": (f"<h1>Menu</h1><p>El plato Quimbal{r} lleva quinua.</p>".encode(),
                            "text/html", f"Quimbal{r}", "seccion"),
            f"{tag}.csv": (f"cliente,deuda\nPrastel{r},40\n".encode(), "text/csv", f"Prastel{r}", "fila"),
            f"{tag}.json": (json.dumps({"cliente": f"Lumbrac{r}"}).encode(), "application/json", f"Lumbrac{r}", None),
        }
        # Fotos y PDF escaneados van al modelo de visión: los prueban el caso 23 (una llamada real)
        # y tests/test_medios.py. El video se genera en el contenedor (PyAV) y solo se guarda.
        gen = subprocess.run(
            ["docker", "run", "--rm", "-e", "NEXUS_DATA_DIR=/tmp", "-v", f"{REPO / 'tests'}:/app/tests:ro", _imagen(),
             "python", "-c", "import base64, sys; from tests import fixtures as f;"
             "sys.stdout.write(base64.b64encode(f.video_from_audio(b'', with_audio=False)).decode())"],
            capture_output=True, text=True, timeout=300,
        )
        if gen.returncode != 0:
            raise Fail("no se pudo generar el video: " + gen.stderr[-300:])
        made = {"mp4": base64.b64decode(gen.stdout)}

        ids = {name: event_id(self.api.upload(name, data, mime)) for name, (data, mime, _, _) in docs.items()}
        bad = {
            f"{tag}.bin": (b"\x00\x01\x02" * 50, "application/octet-stream", "no soportado"),
            f"{tag}.doc": (b"\xd0\xcf\x11\xe0" + b"\x00" * 200, "application/msword", ".docx"),
        }
        bad_ids = {name: event_id(self.api.upload(name, data, mime)) for name, (data, mime, _) in bad.items()}
        video_id = event_id(self.api.upload(f"{tag}-video.mp4", made["mp4"], "video/mp4"))

        problems = []
        for name, eid in ids.items():
            ev = wait_final(self.api, eid, PROCESS_TIMEOUT + 120)
            if ev["status"] != "processed":
                problems.append(f"{name}: {ev['status']} ({ev.get('error')})")
                continue
            _, _, word, key = docs[name]
            hits = [h for h in self.api.ok("GET", f"/search?q={urllib.parse.quote(word)}&k=10") if h["event_id"] == eid]
            if not hits:
                problems.append(f"{name}: /search no encuentra {word!r}")
            elif key and not any(key in (h.get("meta") or {}) for h in hits):
                problems.append(f"{name}: el trozo no guarda '{key}' ({hits[0].get('meta')})")
        for name, eid in bad_ids.items():
            ev = wait_final(self.api, eid, PROCESS_TIMEOUT)
            if ev["status"] != "unsupported" or bad[name][2] not in (ev.get("error") or ""):
                problems.append(f"{name}: {ev['status']} ({ev.get('error')})")
        ev = wait_final(self.api, video_id, AUDIO_TIMEOUT)
        if ev["status"] != "empty" or "solo se guardan" not in (ev.get("error") or ""):
            problems.append(f"video: {ev['status']} ({ev.get('error')})")
        if problems:
            raise Fail("; ".join(problems)[:600])

        r2 = self.api.ok("POST", "/ask", {"question": f"¿Qué producto compró el cliente Velbra{r}?"})
        citas = [s.get("cita") or "" for s in r2.get("sources", [])]
        if not any("hoja Ventas" in c for c in citas):
            raise Fail(f"la cita del Excel no indica la hoja: {citas} · {r2.get('answer', '')[:80]}")
        return f"{len(docs)} formatos encontrados con su origen, 2 no soportados con motivo, video solo guardado"

    def t17_ui_retry(self):
        """La interfaz muestra estado y motivo, y el reintento por elemento funciona."""
        r = uuid.uuid4().hex[:8]
        eid = event_id(self.api.upload(f"viejo-{r}.doc", b"\xd0\xcf\x11\xe0" + r.encode() * 40, "application/msword"))
        ev = wait_final(self.api, eid, PROCESS_TIMEOUT)
        if ev["status"] != "unsupported" or not ev.get("reintentable") or ".docx" not in (ev.get("error") or ""):
            raise Fail(f"la bandeja no muestra el motivo ni permite reintentar: {ev}")
        node = next((n for n in self.api.ok("GET", "/brain/graph")["nodes"] if n["id"] == eid), None)
        if not node or ".docx" not in (node.get("error") or ""):
            raise Fail(f"/brain/graph no trae el motivo: {node}")
        if self.api.ok("POST", f"/requeue/{eid}").get("reencolados") != 1:
            raise Fail("POST /requeue/{id} no reencoló")
        ev = wait_final(self.api, eid, PROCESS_TIMEOUT)
        if ev["status"] != "unsupported":
            raise Fail(f"tras reintentar quedó en '{ev['status']}'")
        ok_id = next(e["id"] for e in self.api.ok("GET", "/inbox?limit=500") if e["status"] == "processed")
        status, _ = self.api.call("POST", f"/requeue/{ok_id}")
        if status != 409:
            raise Fail(f"reintentar algo ya procesado debería dar 409, dio {status}")
        for path, marks in (("/", ("inboxBtn", "Reintentar")), ("/brain", ("Reintentar", "Imagen"))):
            status, html = self.api.call("GET", path, raw=True)
            missing = [m for m in marks if m.encode() not in html]
            if status != 200 or missing:
                raise Fail(f"{path}: HTTP {status}, falta {missing}")
        return "motivo visible en bandeja y cerebro, reintento por elemento, 409 si ya está listo"


TESTS = [
    ("1 health", "t1_health"),
    ("2 texto + search + ask", "t2_text"),
    ("3 duplicado", "t3_duplicate"),
    ("4 txt largo + digest", "t4_long_txt"),
    ("5 pdf", "t5_pdf"),
    ("6 audio", "t6_audio"),
    ("7 pregunta sin relación", "t7_unrelated"),
    ("8 growth y páginas", "t8_pages"),
    ("9 backup + requeue", "t9_backup"),
    ("10 caos (OpenRouter inalcanzable)", "t10_chaos"),
    ("11 canal simulado", "t11_channel"),
    ("12 pruebas unitarias", "t12_unit"),
    ("13 perfiles", "t13_profile"),
    ("14 OpenRouter real", "t14_openrouter"),
    ("15 conocimiento", "t15_knowledge"),
    ("16 formatos", "t16_formats"),
    ("17 interfaz y reintento", "t17_ui_retry"),
    ("18 recordatorio tras reinicio", "t18_reminder_restart"),
    ("19 memoria de la charla", "t19_memoria"),
    ("20 recordatorio a 90 s con botón", "t20_recordatorio_90s"),
    ("21 saludo por iniciativa", "t21_saludo"),
    ("22 internet y olvido", "t22_internet_olvido"),
    ("23 ver y oír", "t23_ver_oir"),
    ("24 web con sesión", "t24_web"),
]

# Caso 23: nota de voz por el camino del bot (SimChannel -> Assistant -> API en el mismo proceso,
# con httpx.ASGITransport) con la transcripción simulada. Corre dentro del contenedor aislado.
OIR_SIMULADO = r"""
import asyncio, io, struct, sys, wave
import httpx
from providers import openrouter
tag = sys.argv[1]
TEXTO = f"Isabel, recuérdame mañana a las 10 am llamar al contador Quelvar{tag}."

async def transcribir(self, audio, formato, idioma="es", vocabulario=()):
    return ({"text": TEXTO, "segments": [{"start": 0, "end": 3, "text": TEXTO, "avg_logprob": -0.2, "no_speech_prob": 0.01}]},
            {"proveedor": "openrouter-audio", "modelo": "simulado", "costo_usd": 0.0})

openrouter.OpenRouter.transcribir = transcribir
from core.main import app
from channels.assistant import Assistant
from channels.client import NexusClient
from channels.simulado import OWNER, SimChannel

def wav():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", (i * 37 + len(tag)) % 2000) for i in range(32000)))
    return buf.getvalue()

async def main():
    ch = SimChannel([OWNER])
    nexus = NexusClient("http://core", transport=httpx.ASGITransport(app=app))
    bot = Assistant(ch, nexus, {OWNER}, pausa=False)
    r = " ".join(await bot.handle(ch.file(wav(), "nota_de_voz.ogg", "audio/ogg", voice=True)))
    pend = await nexus.reminders(OWNER)
    ok = f"Quelvar{tag}" in r and any(f"Quelvar{tag}" in p["text"] for p in pend)
    print(("ok " if ok else "FALLA ") + "la nota de voz crea el recordatorio como si la hubiera escrito -> " + r[:120])
    await nexus.close()

asyncio.run(main())
"""

# Programador acelerado en la instancia aislada: avisos e insistencias en segundos
ISOLATED_ENV = {"NEXUS_SCHEDULER_INTERVAL": "2", "NEXUS_REMINDER_RETRY_MIN": "0.1"}
# Sin tope de gasto en la prueba (la instancia aislada empieza con su propio registro de uso)
ISOLATED_ENV["NEXUS_DAILY_BUDGET_USD"] = ""
# Caso 10: segunda instancia sin salida a OpenRouter (un proxy HTTPS que no responde) y worker rápido
CHAOS_NAME, CHAOS_PORT = "nexus-e2e-caos", 8002
CHAOS_ENV = ISOLATED_ENV | {"HTTPS_PROXY": "http://127.0.0.1:9", "NEXUS_WORKER_INTERVAL": "3"}


def main() -> int:
    p = argparse.ArgumentParser(description="Prueba de extremo a extremo de NEXUS")
    p.add_argument("--url", help="instancia existente (sin esto, se usa una aislada temporal)")
    p.add_argument("--audio", help="archivo de audio a transcribir")
    p.add_argument("--audio-phrase", help="frase que debe aparecer en la transcripción")
    p.add_argument("--chaos", action="store_true", help="simula OpenRouter inalcanzable (proxy que no responde)")
    args = p.parse_args()

    if args.url:
        return run(args.url, args)
    with isolated_instance(ISOLATED_ENV) as url:
        return run(url, args, isolated=True)


def run(url: str, args, isolated: bool = False) -> int:
    suite = Suite(Api(url), args)
    suite.isolated = isolated
    modo = "instancia aislada temporal" if isolated else "instancia existente"
    print(f"NEXUS e2e · corrida {suite.tag} · {url} ({modo})\n")
    results = []
    for label, method in TESTS:
        start = time.monotonic()
        try:
            status, reason = "PASS", getattr(suite, method)()
        except Skip as e:
            status, reason = "SKIP", str(e)
        except Fail as e:
            status, reason = "FAIL", str(e)
        except Exception as e:  # errores de red, JSON inesperado, docker...
            status, reason = "FAIL", f"{type(e).__name__}: {e}"
        secs = time.monotonic() - start
        results.append((label, status, secs, reason))
        print(f"{status:4}  {label} ({secs:.1f} s) {reason}", flush=True)

    width = max(len(r[0]) for r in results)
    print("\n" + "-" * (width + 30))
    for label, status, secs, reason in results:
        print(f"{label:<{width}}  {status:4}  {secs:7.1f} s  {reason[:90]}")
    total = {s: sum(1 for r in results if r[1] == s) for s in ("PASS", "FAIL", "SKIP")}
    print("-" * (width + 30))
    print(f"PASS {total['PASS']} · FAIL {total['FAIL']} · SKIP {total['SKIP']}")
    return 1 if total["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
