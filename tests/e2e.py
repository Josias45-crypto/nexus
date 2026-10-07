"""Prueba de extremo a extremo de NEXUS (solo librería estándar).

Uso:
    python3 tests/e2e.py [--url http://localhost:8000]
                         [--audio ruta.wav --audio-phrase "frase"] [--chaos]

Sin --url levanta una instancia temporal aislada (contenedor nexus-e2e, puerto 8001) con la
misma imagen y el mismo Ollama, pero con datos y respaldos en una carpeta temporal que se
borra al terminar: la memoria real no se toca. Requiere `docker compose up -d` previo.
Con --url prueba contra esa instancia (y deja en ella los datos sintéticos).

Usa solo datos sintéticos marcados con un id de corrida único. --chaos detiene y vuelve a
arrancar el contenedor de Ollama (docker compose stop/start; nunca down).
"""

import argparse
import json
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
NO_INFO = "No tengo información sobre eso en mi memoria."
GROWTH_FIELDS = [
    "dias_de_vida",
    "nacio",
    "aprendido",
    "hoy",
    "racha_dias",
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


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None, headers=None, raw=False):
        data = None
        headers = dict(headers or {})
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


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO, check=True, capture_output=True)


E2E_NAME = "nexus-e2e"
E2E_PORT = 8001


def _docker(*args: str) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"docker {args[0]}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


@contextmanager
def isolated_instance():
    """Contenedor temporal con datos propios; usa la red, la imagen y el caché de Whisper
    de la instalación en marcha (nexus-core y nexus-ollama deben estar arriba)."""
    try:
        image = _docker("inspect", "nexus-core", "-f", "{{.Config.Image}}")
        network = _docker(
            "inspect", "nexus-ollama", "-f",
            "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}",
        )
        whisper = _docker(
            "inspect", "nexus-core", "-f",
            '{{range .Mounts}}{{if eq .Destination "/models"}}{{.Name}}{{end}}{{end}}',
        )
    except RuntimeError as e:
        raise SystemExit(f"Levanta NEXUS antes (docker compose up -d): {e}")

    subprocess.run(["docker", "rm", "-f", E2E_NAME], capture_output=True)
    work = Path(tempfile.mkdtemp(prefix="nexus-e2e-"))
    (work / "data").mkdir()
    (work / "backups").mkdir()
    cmd = [
        "run", "-d", "--rm", "--name", E2E_NAME, "--network", network,
        "-p", f"127.0.0.1:{E2E_PORT}:8000",
        "-v", f"{work / 'data'}:/data", "-v", f"{work / 'backups'}:/backups",
        "-e", "NEXUS_DATA_DIR=/data", "-e", "NEXUS_BACKUP_DIR=/backups",
        "-e", "NEXUS_BACKUP_HOURS=0",
    ]
    if whisper:
        cmd += ["-v", f"{whisper}:/models"]
    if (REPO / ".env").exists():
        cmd += ["--env-file", str(REPO / ".env")]
    try:
        _docker(*cmd, image)
        url = f"http://127.0.0.1:{E2E_PORT}"
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen(url + "/health", timeout=5):
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.monotonic() > deadline:
                    logs = subprocess.run(["docker", "logs", E2E_NAME], capture_output=True, text=True)
                    raise SystemExit("La instancia de prueba no arrancó:\n" + logs.stderr[-1500:])
                time.sleep(2)
        yield url
    finally:
        subprocess.run(["docker", "stop", "-t", "15", E2E_NAME], capture_output=True)
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
        return f"modelo {d.get('llm_model')}, llm_activo {d.get('llm_activo', '?')}"

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
        if not self.args.chaos:
            raise Skip("sin --chaos")
        text = f"Dato sintético de caos {self.tag}: el río ficticio Ondarel{self.rid} fluye hacia el norte."
        compose("stop", "ollama")
        try:
            eid = event_id(self.api.ok("POST", "/inbox/text", {"text": text, "source": "e2e"}))
            # Menos que los reintentos del worker (3 x 20 s): debe sobrevivir sin quedar 'failed'
            time.sleep(25)
        finally:
            compose("start", "ollama")
        ev = wait_status(self.api, eid, PROCESS_TIMEOUT + 120)
        return f"processed tras reiniciar Ollama (intentos {ev.get('attempts')})"


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
    ("10 caos (Ollama caído)", "t10_chaos"),
]


def main() -> int:
    p = argparse.ArgumentParser(description="Prueba de extremo a extremo de NEXUS")
    p.add_argument("--url", help="instancia existente (sin esto, se usa una aislada temporal)")
    p.add_argument("--audio", help="archivo de audio a transcribir")
    p.add_argument("--audio-phrase", help="frase que debe aparecer en la transcripción")
    p.add_argument("--chaos", action="store_true", help="detiene Ollama un momento (docker compose)")
    args = p.parse_args()

    if args.url:
        return run(args.url, args)
    with isolated_instance() as url:
        return run(url, args, isolated=True)


def run(url: str, args, isolated: bool = False) -> int:
    suite = Suite(Api(url), args)
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
