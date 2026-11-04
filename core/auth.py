"""Acceso a la web y a la API (Fase 5).

- Todas las rutas exigen sesión (cookie HttpOnly, SameSite=Strict) salvo /login. Los canales
  (Telegram, WhatsApp, simulado), el healthcheck y las pruebas usan un token interno en la cabecera
  X-Nexus-Token.
- Contraseña única, guardada solo como hash scrypt con sal en data/interno/web.json. Se crea o
  cambia en la terminal: `docker compose exec core python -m core.auth` (si no hay terminal
  interactiva, genera una al azar y la muestra una sola vez). Nunca se registra.
- Token interno: NEXUS_INTERNAL_TOKEN si existe; si no, data/interno/token (se genera al
  arrancar, permisos 600). El contenedor de Telegram lo lee montado en solo lectura.
- Intentos fallidos por dirección: espera creciente (1, 2, 4, 8 s… hasta 15 min).
- Comparaciones de tiempo constante (hmac.compare_digest). Sesiones en la tabla sesiones, solo
  con la huella del token, 30 días.
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import timedelta
from pathlib import Path

from config import settings
from core import clock
from core.db import connect

INTERNO = Path(settings.DATA_DIR) / "interno"
COOKIE = "isabelle_sesion"
CABECERA = "X-Nexus-Token"
SESION_DIAS = 30
ESPERA_MAX = 15 * 60
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
CLAVE_MIN = 10


def _escribir(path: Path, texto: str) -> None:
    INTERNO.mkdir(parents=True, exist_ok=True)
    os.chmod(INTERNO, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(texto)
    os.replace(tmp, path)


_token: str | None = None


def token_interno() -> str:
    """El token de los canales: de la variable, o del archivo (se crea si falta)."""
    global _token
    if _token is None:
        if t := os.getenv("NEXUS_INTERNAL_TOKEN", "").strip():
            _token = t
        else:
            path = INTERNO / "token"
            if not path.exists():
                _escribir(path, secrets.token_urlsafe(32))
            _token = path.read_text().strip()
    return _token


# ---------- contraseña ----------


def _hash(clave: str, sal: bytes) -> bytes:
    return hashlib.scrypt(clave.encode("utf-8"), salt=sal, **SCRYPT)


def hay_clave() -> bool:
    return (INTERNO / "web.json").exists()


def fijar_clave(clave: str) -> None:
    if len(clave) < CLAVE_MIN:
        raise ValueError(f"La contraseña debe tener al menos {CLAVE_MIN} caracteres.")
    sal = secrets.token_bytes(16)
    _escribir(INTERNO / "web.json", json.dumps({"sal": sal.hex(), "hash": _hash(clave, sal).hex(), **SCRYPT}))
    with connect() as conn:  # cambiar la contraseña cierra todas las sesiones
        conn.execute("DELETE FROM sesiones")


def verificar_clave(clave: str) -> bool:
    try:
        d = json.loads((INTERNO / "web.json").read_text())
    except (OSError, ValueError):
        return False
    calc = hashlib.scrypt((clave or "").encode("utf-8"), salt=bytes.fromhex(d["sal"]),
                          n=d["n"], r=d["r"], p=d["p"], dklen=d["dklen"])
    return hmac.compare_digest(calc, bytes.fromhex(d["hash"]))


# ---------- sesiones ----------


def _huella(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear_sesion() -> str:
    token = secrets.token_urlsafe(32)
    now = clock.now_utc()
    with connect() as conn:
        conn.execute("DELETE FROM sesiones WHERE expira < ?", (now.isoformat(),))
        conn.execute("INSERT INTO sesiones (huella, creada, expira) VALUES (?, ?, ?)",
                     (_huella(token), now.isoformat(), (now + timedelta(days=SESION_DIAS)).isoformat()))
    return token


def sesion_valida(token: str | None) -> bool:
    if not token:
        return False
    with connect() as conn:
        r = conn.execute("SELECT 1 FROM sesiones WHERE huella = ? AND expira > ?",
                         (_huella(token), clock.now_utc().isoformat())).fetchone()
    return bool(r)


def cerrar_sesion(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sesiones WHERE huella = ?", (_huella(token),))


def cifrada(esquema: str, reenviado: str | None) -> bool:
    """¿La conexión del navegador va por HTTPS? (directa o tras un proxy con TLS)."""
    return esquema == "https" or (reenviado or "").split(",")[0].strip().lower() == "https"


def cookie_segura(es_https: bool) -> bool:
    """Secure en la cookie: por HTTP no se puede (el navegador no la devolvería y el login fallaría)."""
    modo = settings.COOKIE_SECURE
    return True if modo == "on" else False if modo == "off" else es_https


def autorizado(cookie: str | None, cabecera: str | None) -> bool:
    if cabecera and hmac.compare_digest(cabecera.encode(), token_interno().encode()):
        return True
    return sesion_valida(cookie)


# ---------- intentos ----------

_fallos: dict[str, tuple[int, float]] = {}  # dirección -> (fallos seguidos, cuándo puede volver)


def espera(ip: str) -> int:
    """Segundos que esa dirección debe esperar antes de otro intento (0 = puede)."""
    _, hasta = _fallos.get(ip, (0, 0.0))
    return max(0, int(hasta - time.monotonic() + 0.999))


def fallo(ip: str) -> int:
    n = _fallos.get(ip, (0, 0.0))[0] + 1
    s = min(ESPERA_MAX, 2 ** (n - 1))
    _fallos[ip] = (n, time.monotonic() + s)
    return s


def exito(ip: str) -> None:
    _fallos.pop(ip, None)


# ---------- terminal ----------


def main() -> int:
    """Crea o cambia la contraseña de la web (y cierra las sesiones abiertas)."""
    from core.db import init_db

    init_db()
    if sys.stdin.isatty():
        import getpass

        clave = getpass.getpass("Contraseña nueva para la web: ")
        if clave != getpass.getpass("Repítela: "):
            print("No coinciden; no cambié nada.")
            return 1
    else:
        clave = secrets.token_urlsafe(15)
        print(f"Contraseña generada (guárdala, no se vuelve a mostrar): {clave}")
    try:
        fijar_clave(clave)
    except ValueError as exc:
        print(exc)
        return 1
    print("Listo: contraseña guardada (solo su hash) y sesiones anteriores cerradas.")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["salud"]:  # healthcheck de Docker: /health con el token interno
        import urllib.request

        req = urllib.request.Request("http://localhost:8000/health", headers={CABECERA: token_interno()})
        urllib.request.urlopen(req, timeout=5)
        sys.exit(0)
    sys.exit(main())
