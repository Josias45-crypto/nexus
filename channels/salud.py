"""Healthcheck de los canales (docker-compose.yml): el núcleo responde /health con el token interno.

    python -m channels.salud   # sale con 0 si el núcleo responde, 1 si no
"""

import os
import sys
import urllib.request

from channels.client import token_interno


def main() -> int:
    url = os.getenv("NEXUS_API_URL", "http://core:8000").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Nexus-Token": token_interno()}), timeout=5):
            return 0
    except Exception as exc:  # sin la URL ni el token en el mensaje
        print(f"El núcleo no responde ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
