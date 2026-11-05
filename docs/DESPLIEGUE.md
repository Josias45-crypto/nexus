# Despliegue en un VPS compartido (Ubuntu)

El VPS ya corre otro bot (Carla, con systemd y el puerto 8000). Isabelle convive sin tocarlo:

- proyecto de compose fijo `isabelle` (contenedores `isabelle-core-1`, `isabelle-telegram-1`; sin
  nombres fijos ni volúmenes con nombre: todo vive en `~/isabelle/data` y `~/isabelle/backups`);
- el núcleo publica **solo en 127.0.0.1** (`NEXUS_BIND`, por defecto) y en `NEXUS_PORT=8100` (no
  choca con el 8000 de Carla); publicar por HTTP es opcional y con riesgos (paso 7b);
- **no se abre ningún puerto nuevo**: la web se usa por túnel SSH; el bot de Telegram no recibe
  conexiones (long polling);
- límites de memoria (núcleo 1 GB, bot 256 MB), `restart: unless-stopped`, healthchecks y logs de
  Docker rotados (10 MB × 3 por servicio).

Ningún modelo corre en el servidor (todo va por OpenRouter): 2 GB de RAM y 2 GB de disco libres
alcanzan, más tus datos.

## 1. Usuario propio

En el VPS, con tu usuario de administración:

```bash
sudo adduser --disabled-password --gecos "" isabelle
sudo mkdir -p /home/isabelle/.ssh
sudo cp ~/.ssh/authorized_keys /home/isabelle/.ssh/   # tu llave pública (solo llave, sin contraseña)
sudo chown -R isabelle:isabelle /home/isabelle/.ssh && sudo chmod 700 /home/isabelle/.ssh
```

## 2. Docker

Si el VPS aún no tiene Docker (comprueba con `docker --version` y `docker compose version`):

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker isabelle
```

Docker no cambia nada de Carla (systemd). Si el VPS usa `ufw`, no hace falta tocarlo: Isabelle no
publica nada hacia afuera.

## 3. Clave de despliegue de solo lectura

Como `isabelle` (`sudo -iu isabelle`):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/isabelle_deploy -N "" -C "isabelle@vps (solo lectura)"
cat ~/.ssh/isabelle_deploy.pub
cat >> ~/.ssh/config <<'CFG'
Host github-isabelle
  HostName github.com
  User git
  IdentityFile ~/.ssh/isabelle_deploy
  IdentitiesOnly yes
CFG
```

En GitHub: repositorio → Settings → Deploy keys → Add deploy key, pega la llave pública y deja
**sin marcar** «Allow write access». Luego:

```bash
git clone github-isabelle:Josias45-crypto/nexus.git ~/isabelle && cd ~/isabelle
```

## 4. Detener Isabelle en la laptop

Un bot de Telegram solo puede tener un proceso leyendo mensajes: antes de copiar los datos, para
el de la laptop (sin `-v`, los datos no se tocan):

```bash
# En la laptop, en la carpeta del repo. Las instalaciones previas a este cambio se llaman «nexus»:
docker compose -p nexus --profile channels down
docker compose --profile channels down          # (si ya corrías el proyecto «isabelle»)
```

## 5. Copiar el .env y los datos desde la laptop

Con todo detenido (la base no cambia mientras se copia):

```bash
# En la laptop
rsync -a --chmod=F600 .env isabelle@VPS:isabelle/.env
rsync -a --partial data/ isabelle@VPS:isabelle/data/
rsync -a --partial backups/ isabelle@VPS:isabelle/backups/   # opcional: el historial de copias
```

El `.env` lleva las claves: viaja solo por SSH y queda con permisos 600. `data/` trae la base, los
originales, la contraseña de la web (solo su hash) y el token interno.

En el VPS, agrega el puerto (es la única línea que cambia respecto de la laptop):

```bash
cd ~/isabelle && echo "NEXUS_PORT=8100" >> .env
```

## 6. Arrancar

```bash
cd ~/isabelle
docker compose --profile channels up -d --build
docker compose ps                                 # core «healthy» y telegram «healthy»
docker compose logs --tail 20 telegram            # «Bot @... conectado»
curl -s -o /dev/null -w '%{http_code}\n' localhost:8100/health   # 401: pide sesión (está bien)
```

Si no copiaste `data/` o es una instalación nueva, crea la contraseña de la web (pide la
contraseña dos veces; se guarda solo su hash):

```bash
docker compose exec core python -m core.auth
```

Comprobar que no se abrió nada hacia afuera: `sudo ss -ltnp | grep 8100` debe mostrar
`127.0.0.1:8100` y nunca `0.0.0.0:8100`.

## 7. Usar la web por túnel SSH

Desde la laptop:

```bash
ssh -N -L 8100:127.0.0.1:8100 isabelle@VPS
```

y abre <http://localhost:8100>. El túnel solo vive mientras esa terminal esté abierta.

## 7b. Opcional: publicar la web por HTTP (sin túnel)

El modo por defecto (127.0.0.1 + túnel SSH) es el seguro. Si necesitas entrar desde un navegador
sin túnel (por ejemplo, desde el celular), se puede publicar el puerto directamente:

```bash
cd ~/isabelle
echo "NEXUS_BIND=0.0.0.0" >> .env          # junto a NEXUS_PORT=8100
docker compose --profile channels up -d    # recrea el núcleo con el puerto publicado
sudo ss -ltnp | grep 8100                  # ahora: 0.0.0.0:8100
```

Abre `http://IP-DEL-VPS:8100`. El login muestra **«Conexión sin cifrar»** y la cookie de sesión va
sin `Secure` (por HTTP el navegador no la devolvería), pero sigue con HttpOnly y SameSite=Strict.
`NEXUS_COOKIE_SECURE` (`auto` por defecto: según la conexión o el `X-Forwarded-Proto` de un proxy
con TLS; `on` o `off` para forzarlo).

**Riesgos de este modo:**

- La contraseña y la cookie de sesión viajan **sin cifrar**: en un wifi público, en la red del
  trabajo o en el camino, alguien puede leerlas y entrar con tu sesión (todo lo guardado, la
  charla y los originales). Úsalo solo desde redes de confianza.
- El puerto queda expuesto a internet y lo encuentran los escáneres. Tras cada fallo la espera
  crece (1, 2, 4… s, hasta 15 min) y el intento se registra (sin la contraseña), pero usa una
  contraseña larga (16 caracteres o más): `docker compose exec core python -m core.auth`.
- **Docker publica el puerto aunque `ufw` no lo permita** (sus reglas de iptables van antes que
  las de ufw): no cuentes con ufw para cerrarlo; ciérralo volviendo al modo seguro. Si el
  proveedor del VPS tiene un firewall propio (panel web), ese sí puede limitarlo a tus IP.
- Mejor alternativa si lo usas a menudo: HTTPS con un proxy (Caddy o nginx con un dominio) que
  escuche en 443 y reenvíe a `127.0.0.1:8100`, dejando `NEXUS_BIND` en 127.0.0.1; la cookie
  pasa sola a `Secure` por `X-Forwarded-Proto: https` y desaparece el aviso.

**Volver al modo seguro:**

```bash
cd ~/isabelle
sed -i '/^NEXUS_BIND=/d' .env              # o NEXUS_BIND=127.0.0.1
docker compose --profile channels up -d
sudo ss -ltnp | grep 8100                  # de nuevo solo 127.0.0.1:8100
docker compose exec core python -m core.auth   # contraseña nueva: cierra todas las sesiones
```

Cambiar la contraseña al volver es lo prudente: cualquier sesión que se haya visto pasar por HTTP
deja de servir.

## 8. Actualizar y volver atrás

Actualizar (antes, una copia verificada y una etiqueta de las imágenes actuales):

```bash
cd ~/isabelle
H="X-Nexus-Token: $(cat data/interno/token)"
curl -s -X POST -H "$H" localhost:8100/backup          # copia verificada de la base
docker tag isabelle-core:latest isabelle-core:anterior
docker tag isabelle-telegram:latest isabelle-telegram:anterior
git rev-parse --short HEAD > .version-anterior
git pull --ff-only
docker compose --profile channels up -d --build
docker compose ps
```

Volver atrás (el código y las imágenes de antes; los datos no se tocan):

```bash
cd ~/isabelle
git checkout "$(cat .version-anterior)"
docker tag isabelle-core:anterior isabelle-core:latest
docker tag isabelle-telegram:anterior isabelle-telegram:latest
docker compose --profile channels up -d --no-build
```

Si además la base quedó dañada: detén todo (`docker compose --profile channels down`, nunca `-v`),
copia la última `backups/db/nexus-*.db` sobre `data/nexus.db` y vuelve a levantar (detalle en
[OPERACION](OPERACION.md#restaurar)). Al terminar: `git checkout main`.

## 9. Respaldos hacia la laptop

El núcleo hace una copia verificada cada `NEXUS_BACKUP_HOURS` (24) en `~/isabelle/backups` y
guarda 7. Para traerlas a la laptop:

```bash
# En la laptop
scripts/respaldo_remoto.sh isabelle@VPS               # → ~/respaldos-isabelle
crontab -e    # cada día a las 13:00:
# 0 13 * * * /ruta/al/repo/scripts/respaldo_remoto.sh isabelle@VPS >> ~/respaldos-isabelle.log 2>&1
```

El script solo lee del servidor (rsync sin `--delete`) y avisa si la última copia tiene más de 48 h.

## Qué vigilar

Si un recordatorio no llegó, corre en el servidor (solo lee; no muestra el texto de ningún mensaje):

```bash
cd ~/isabelle && scripts/diagnostico_remoto.sh
```

Imprime la hora y la zona del servidor y del contenedor, el estado del programador (`/health`:
`recordatorios_atrasados`, marca de vida, vigilante), los últimos 20 recordatorios y sus avisos con
la demora de entrega y el acuse de Telegram, el buzón pendiente y los errores de Telegram de 24 h.

| Qué | Cómo |
|---|---|
| Recordatorios | `scripts/diagnostico_remoto.sh` (atrasados = 0, programador vivo) |
| Contenedores | `docker compose ps` (los dos «healthy») |
| Memoria | `docker stats --no-stream` (núcleo < 1 GB, bot < 256 MB) |
| Logs | `docker compose logs --tail 50 core` (rotan solos: 10 MB × 3) |
| Carla intacta | `systemctl status <servicio de Carla>` y su puerto 8000 sigue igual |
