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

| Le escribes | Qué hace Isabelle |
|---|---|
| cualquier cosa, en lenguaje natural | Conversa con su personalidad (`profiles/isabelle.toml`); sin comandos |
| «recuerda esto: …» (o solo «recuerda esto») | Lo guarda tal cual (o guarda tu mensaje anterior) |
| un dato suelto («el local cierra a las 8») | Lo guarda y lo comenta; si ve un riesgo, te lo dice |
| una pregunta | Busca en su memoria y cita las fuentes; si no está, responde con lo que sabe en general y lo avisa |
| «recuérdame mañana a las 9 …» | Lo guarda sin preguntar, dice lo que entendió y muestra **Cambiar** y **Cancelar**; solo pregunta si la hora es dudosa |
| «¿qué recordatorios tengo?», «ya lo hice», «cancela el recordatorio 3» | Lista, marca hecho o cancela (cancelar pide confirmación) |
| «analiza a fondo…», «piénsalo bien…», «dame tu opinión…» | Usa el modelo potente (`NEXUS_OPENROUTER_MODEL_DEEP`) y avisa que cuesta más |
| «¿qué puedes hacer?» | La ayuda, en lenguaje natural |
| archivo, foto, nota de voz | Se guarda el original y se procesa; Isabelle acusa recibo |
| «ok ok», «gracias», «hasta mañana» | Cierra la charla sin otra pregunta |

Horas que entiende: `4:17`, `4 : 17`, `4.17`, `4h17`, `4 y 18`, `4 y cuarto`, `4 y media`,
`4 menos cuarto`, `16:17`, `4 pm`, `de la mañana/tarde/noche`, `mediodía`, `medianoche`,
`mañana 9`, `en 20 minutos`, `en 90 segundos`. Sin am/pm ni fecha elige la próxima vez que llega esa hora (a las
04:15, "a las 4" es 16:00 y "a las 4:17" es 04:17). Con fecha, de 1 a 7 es de la tarde. Si la
hora no queda clara o sobra un número ("a las 4 17 …"), pregunta "¿A qué hora exactamente?" en
vez de adivinar (y, cuando aclaras, pone esa hora en el pedido); si pides una hora con am/pm que
hoy ya pasó, pregunta si es para mañana. Con hora clara lo guarda sin preguntar:

    Listo, jefe: hoy a las 4:17 a. m.: llamar a Piter.
    [Cambiar] [Cancelar]

«Cambiar» pregunta la nueva hora («mejor a las 10» conserva el día); «Cancelar» lo cancela al
instante. Lo que espera respuesta (la hora nueva, el «¿Cancelo el recordatorio n.º 3?» o la hora
dudosa) queda en la base, no en la memoria del bot: sobrevive a un reinicio y caduca a los 15
minutos. Solo dice «Listo» tras leer la fila escrita, y un «sí» repetido no la duplica.

**Seguimiento (sin IA).** Si se creó con más de 30 min de anticipación, avisa 30 min antes. A la
hora exacta avisa con **Ya lo hice** y **Recuérdame**; si no respondes, insiste cada 20 min,
como máximo 3 veces (cada vez más suave). Tras la tercera queda «sin confirmar» y deja de
insistir (el saludo de la Fase 2D lo mencionará); «ya lo hice» lo sigue cerrando. «Ya lo hice»,
cancelar o posponer lo detienen al instante. **Recuérdame** pregunta «¿cuándo, jefe?» y entiende
«en un día», «mañana a las 9», «el viernes a las 3»; si no entiende, repregunta con botones (En 1
hora, Mañana 9:00 a. m., En 1 día). Lo programado suena a su hora aunque sea de noche. Si el
sistema estuvo caído, lo vencido sale con «(retrasado)», un aviso a la vez.

**Repetición:** «todos los lunes a las 8 …», «cada día a las 7 …», «el 15 de cada mes …». Cada
ocurrencia es independiente: «ya lo hice» cierra solo la actual y se programa la siguiente. Al
cancelar una de una serie pregunta **Solo este** o **Toda la serie**.

**En lenguaje natural:** «ya lo hice», «cancela el 3», «pospón el 2 una hora», «¿qué tengo
pendiente?» (lista en hora de Perú con su estado). Los textos salen de `[recordatorios]` en
`profiles/isabelle.toml`, con varias variantes que no se repiten seguidas.

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
6. Escríbele «hola» a tu bot.

Cualquier otra persona que le escriba al bot es ignorada: no se le responde y sus archivos no
se descargan.

Límite: la API de bots solo deja descargar archivos de **hasta 20 MB**.

> **Privacidad.** Los chats con bots de Telegram **no tienen cifrado de extremo a extremo**:
> Telegram puede ver los mensajes en sus servidores: lo que escribes o envías por el chat pasa
> por Telegram. Para información muy sensible usa la interfaz web
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
