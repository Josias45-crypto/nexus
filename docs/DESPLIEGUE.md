# Despliegue en un VPS compartido (Ubuntu)

El VPS ya corre otro bot (Carla, con systemd y el puerto 8000). Isabelle convive sin tocarlo:

- proyecto de compose fijo `isabelle` (contenedores `isabelle-core-1`, `isabelle-telegram-1`; sin
  nombres fijos ni volúmenes con nombre: todo vive en `~/isabelle/data` y `~/isabelle/backups`);
- el núcleo publica **solo en 127.0.0.1** y en `NEXUS_PORT=8100` (no choca con el 8000 de Carla);
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

| Qué | Cómo |
|---|---|
| Contenedores | `docker compose ps` (los dos «healthy») |
| Memoria | `docker stats --no-stream` (núcleo < 1 GB, bot < 256 MB) |
| Logs | `docker compose logs --tail 50 core` (rotan solos: 10 MB × 3) |
| Carla intacta | `systemctl status <servicio de Carla>` y su puerto 8000 sigue igual |
