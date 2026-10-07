# Canales: hablar con NEXUS fuera del navegador

NEXUS es el asistente **del dueño**: los canales sirven para que tú le mandes información,
archivos y notas de voz, le preguntes y recibas sus avisos. No está pensado para atender a
clientes finales.

## Cómo está armado

```
Telegram / WhatsApp / simulado          núcleo (core)
┌──────────────────────────┐   HTTP   ┌──────────────────────────────┐
│ channels/<canal>.py      │ ───────► │ /ask /inbox /reminders ...   │
│   recibe y envía         │          │                              │
│ channels/assistant.py    │ ◄─────── │ /outbox  (avisos pendientes) │
│   comandos, confirmación │          │   ▲ programador del worker   │
└──────────────────────────┘          └──────────────────────────────┘
```

- Cada canal solo sabe recibir y enviar en su plataforma (`channels/base.py`).
- La conversación es común a todos (`channels/assistant.py`) y usa la API de NEXUS.
- Lo que NEXUS quiere decir por iniciativa propia (recordatorios, resumen matutino) queda en
  el **buzón de salida** (`outbox`). Cada canal lo recoge y confirma la entrega. El núcleo no
  conoce ningún token de mensajería.

## Comandos

| Mensaje | Qué hace |
|---|---|
| texto libre o `/pregunta …` | Pregunta a la memoria y cita las fuentes |
| `/recordar …` | Guarda una nota |
| archivo, foto, nota de voz | Va a la bandeja: se guarda el original y se procesa |
| `recuérdame mañana a las 9 …` | Interpreta la fecha y **pide confirmación** (sí/no) antes de guardar |
| `/recordatorios` | Lista los pendientes |
| `/hecho [n]` | Marca hecho el recordatorio n (sin número: el último que te avisó) |
| `/hoy` | Lo que aprendió hoy y los recordatorios del día |
| `/estado` | Crecimiento: días de vida, racha, elementos y pendientes |
| `/privado [texto]` | Marca como privado el siguiente envío (o guarda ese texto como privado) |
| `/ayuda` | Ayuda con ejemplos del perfil activo |

Si un recordatorio vence y no respondes `/hecho`, NEXUS insiste cada
`NEXUS_REMINDER_RETRY_MIN` minutos hasta `NEXUS_REMINDER_MAX_TRIES` avisos.

El resumen matutino se envía a la hora `resumen_matutino` del perfil (hora local según
`NEXUS_TZ_OFFSET`). Si NEXUS estuvo apagado, se envía con hasta 3 h de atraso; después, ese
día se omite.

## Telegram (recomendado)

Usa *long polling*: NEXUS consulta a Telegram, así que **no hay que abrir puertos**.

1. En Telegram, abre **@BotFather**, envía `/newbot`, elige un nombre y un usuario que termine
   en `bot`. Copia el token que te da.
2. En `.env`, escribe `TELEGRAM_BOT_TOKEN=<token>`. No lo compartas ni lo subas al repositorio.
3. Averigua tu id numérico: escríbele a **@userinfobot**. Si prefieres, arranca el bot con
   `TELEGRAM_ALLOWED_IDS` vacío, escríbele y busca `usuario no autorizado (id …)` en
   `docker logs nexus-telegram`.
4. En `.env`, escribe `TELEGRAM_ALLOWED_IDS=<tu id>` (varios, separados por comas).
5. Arranca: `docker compose --profile channels up -d`.
6. Escríbele `/start` a tu bot.

Cualquier otra persona que le escriba al bot es ignorada: no se le responde y sus archivos no
se descargan.

Límite: la API de bots solo deja descargar archivos de **hasta 20 MB**.

> **Privacidad.** Los chats con bots de Telegram **no tienen cifrado de extremo a extremo**:
> Telegram puede ver los mensajes en sus servidores. NEXUS reduce lo que viaja (en `/hoy` y en
> el resumen, el contenido privado aparece solo como "(privado)"), pero lo que tú escribes o
> envías por el chat sí pasa por Telegram. Para información muy sensible usa la interfaz web
> por Tailscale.

## WhatsApp (oficial, apagado por defecto)

Solo por la **WhatsApp Business Cloud API de Meta**. No se usan librerías no oficiales, que
violan los términos y pueden hacer que bloqueen el número.

Lo que exige:
- Una cuenta de **Meta Business** y una app en developers.facebook.com con el producto WhatsApp.
- Un número de WhatsApp Business (no puede estar en uso en la app normal de WhatsApp).
- Una **dirección pública con HTTPS** que llegue al webhook (puerto 8081 del anfitrión), por
  ejemplo Caddy o Tailscale Funnel. Esto **expone un puerto a Internet**, a diferencia de
  Telegram.
- Ventana de 24 h: NEXUS solo puede escribirte primero si le escribiste en las últimas 24 h.
  Fuera de esa ventana Meta exige plantillas aprobadas; NEXUS descarta ese aviso y lo registra.

Configuración: llena las variables `WHATSAPP_*` de `.env.example` y pon `NEXUS_WHATSAPP=on`.
Luego arranca con `docker compose --profile whatsapp up -d` y registra en Meta el webhook
`https://<tu-dominio>/webhook` con tu `WHATSAPP_VERIFY_TOKEN`. Cada mensaje se valida con la
firma `X-Hub-Signature-256` (App Secret); los que no traen una firma válida se rechazan.

## Canal simulado

`channels/simulado.py` usa la misma lógica sin red externa. Sirve para probar:
`python3 tests/e2e.py` lo ejecuta contra la instancia aislada.

## Llamadas telefónicas (fuera de alcance; nota de diseño)

NEXUS no hace ni recibe llamadas. Alternativas, si algún día hacen falta:

| Opción | Costo aproximado | Privacidad |
|---|---|---|
| Nota de voz por Telegram (ya funciona) | Gratis | El audio pasa por Telegram; la transcripción es local |
| Telefonía en la nube (Twilio, Vonage) + Whisper local | Número ~1–2 USD/mes + minutos | El audio de la llamada pasa por el proveedor |
| Centralita propia (Asterisk/FreeSWITCH) con un troncal SIP | Troncal SIP + servidor | La más privada, pero la más compleja de operar |
| Voz en tiempo real con modelos en la nube | Por minuto, alto | El audio sale a terceros: no apto para datos reales |

Encajaría como un canal más (`channels/voz.py`): convierte la llamada en audio, la manda a
`/inbox/file` y lee la respuesta con TTS local. El núcleo no cambiaría.
