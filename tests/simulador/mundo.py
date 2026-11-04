"""Un «mundo» del simulador: Isabelle entera en un proceso, con reloj simulado.

Corre dentro de un contenedor desechable (datos en una carpeta temporal; la memoria real no se toca).
El jefe simulado habla por el MISMO camino del bot real: SimChannel (Telegram simulado) ->
channels.assistant.Assistant -> NexusClient -> API (ASGITransport, en el mismo proceso) ->
conversa -> OpenRouter real. El reloj (core.clock.now_utc) es simulado: avanzar() salta de evento en
evento (el próximo aviso, el próximo saludo) corriendo el programador, los saludos y el reparto del
buzón como lo harían los bucles reales, y anota la hora simulada de cada entrega.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx

from core import clock

INICIO = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)  # lunes 2 de noviembre, 8:00 a. m. en Perú
OWNER = "1001"


class Reloj:
    def __init__(self, t: datetime = INICIO):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Mundo:
    """Isabelle con un reloj propio. Usar dentro de un único asyncio.run (el bot y el cliente HTTP
    viven en ese bucle)."""

    def __init__(self, inicio: datetime = INICIO):
        self.reloj = Reloj(inicio)
        self._parche = mock.patch.object(clock, "now_utc", self.reloj)
        self._parche.start()
        from channels.assistant import Assistant
        from channels.client import NexusClient
        from channels.simulado import SimChannel
        from core.main import app

        self.app = app
        self.ch = SimChannel([OWNER])
        self.nx = NexusClient("http://isabelle", transport=httpx.ASGITransport(app=app))
        self.bot = Assistant(self.ch, self.nx, {OWNER}, pausa=False)
        self.bot.max_descarga = 20 * 1024 * 1024  # como Telegram
        self.bot.rafaga_s = 0.3  # junta la ráfaga sin alargar la simulación (en el bot real: 1,2 s)
        self.entregas: list[dict] = []  # lo que Isabelle mandó por su cuenta (avisos, saludos, alertas)
        self.turnos: list[dict] = []  # la conversación: jefe e Isabelle con su hora simulada

    async def cerrar(self) -> None:
        await self.nx.close()
        self._parche.stop()

    # ---------- hora ----------

    @property
    def ahora(self) -> datetime:
        return self.reloj.t

    def local(self) -> datetime:
        return clock.now_local()

    # ---------- hablar ----------

    def _anotar(self, rol: str, texto: str, **extra) -> None:
        self.turnos.append({"t": self.reloj.t.isoformat(), "rol": rol, "texto": texto, **extra})

    async def decir(self, texto: str) -> str:
        self._anotar("jefe", texto)
        t0 = time.monotonic()
        r = "\n".join(await self.bot.handle(self.ch.text(texto)))
        self._anotar("isabelle", r, segundos=round(time.monotonic() - t0, 2), botones=self.ch.buttons)
        await self.repartir()
        return r

    async def rafaga(self, textos: list[str]) -> list[str]:
        """Varios mensajes seguidos mientras ella piensa: el canal los junta en una respuesta."""
        for t in textos:
            self._anotar("jefe", t)
        tareas = [asyncio.create_task(self.bot.handle(self.ch.text(t))) for t in textos]
        resultados = await asyncio.gather(*tareas)
        r = "\n".join(x for res in resultados for x in res)
        self._anotar("isabelle", r, rafaga=len(textos), respuestas=sum(1 for res in resultados if res))
        return [x for res in resultados for x in res]

    def _botones_de(self, contiene: str):
        for (_, texto), b in zip(reversed(self.ch.sent), reversed(self.ch.botones_de)):
            if contiene.lower() in texto.lower() and b:
                return b
        return None

    async def boton(self, etiqueta: str, de: str | None = None) -> str:
        botones = self._botones_de(de) if de else self.ch.buttons
        dato = next((b["dato"] for fila in botones or [] for b in fila if b["texto"] == etiqueta), None)
        if not dato:
            self._anotar("jefe", f"(pulsa «{etiqueta}»: no hay ese botón)")
            return ""
        from channels.base import Incoming

        self._anotar("jefe", f"(pulsa «{etiqueta}»)")
        r = "\n".join(await self.bot.handle(Incoming("simulado", chat_id=OWNER, user_id=OWNER, button=dato)))
        self._anotar("isabelle", r, botones=self.ch.buttons)
        return r

    async def archivo(self, data: bytes, nombre: str, mime: str, leyenda: str = "", voz: bool = False,
                      grupo: str | None = None, tamano: int | None = None) -> str:
        from channels.base import Incoming

        async def bajar() -> bytes:
            return data

        self._anotar("jefe", f"(envía «{nombre}», {len(data)} bytes)" + (f" {leyenda}" if leyenda else ""))
        inc = Incoming("simulado", chat_id=OWNER, user_id=OWNER, text=leyenda, filename=nombre, mime=mime,
                       is_voice=voz, size=tamano or len(data), download=bajar, grupo=grupo)
        r = "\n".join(await self.bot.handle(inc))
        self._anotar("isabelle", r, botones=self.ch.buttons)
        return r

    async def album(self, archivos: list[tuple[bytes, str, str]], leyenda: str = "") -> str:
        from channels.base import Incoming

        self._anotar("jefe", f"(envía un álbum de {len(archivos)} fotos) {leyenda}".strip())
        incs = []
        for i, (data, nombre, mime) in enumerate(archivos):
            async def bajar(d=data) -> bytes:
                return d

            incs.append(Incoming("simulado", chat_id=OWNER, user_id=OWNER, text=leyenda if i == 0 else "", filename=nombre,
                                 mime=mime, size=len(data), download=bajar, grupo="album-" + str(len(self.turnos))))
        res = await asyncio.gather(*(self.bot.handle(i) for i in incs))
        r = "\n".join(x for xs in res for x in xs)
        self._anotar("isabelle", r, respuestas=sum(1 for xs in res if xs))
        return r

    # ---------- el tiempo pasa ----------

    async def repartir(self) -> int:
        """El bot recoge el buzón (como su bucle de cada 5 s) y anota la hora simulada de cada entrega."""
        antes = len(self.ch.sent)
        n = await self.bot.deliver_outbox()
        for chat, texto in self.ch.sent[antes:]:
            self.entregas.append({"t": self.reloj.t.isoformat(), "texto": texto, "botones": self.ch.botones_de[-1]})
            self._anotar("isabelle (por su cuenta)", texto)
        return n

    async def procesar(self) -> None:
        """El worker: procesa lo que entró (trozos y vectores) y resume lo largo."""
        from core import digest, processor

        await processor.process_pending(20)
        await digest.digest_pending(5)

    async def _ciclo(self) -> None:
        from core import iniciativa, reminders

        await asyncio.to_thread(reminders.due_step)
        try:
            await iniciativa.due_step()
        except Exception as exc:  # como el bucle real: un saludo que falla no frena nada
            self._anotar("sistema", f"saludo falló: {type(exc).__name__}")
        await self.repartir()

    def _proximo_evento(self, hasta: datetime) -> datetime:
        from core import iniciativa, reminders

        candidatos = [hasta]
        if p := reminders.proximo():
            candidatos.append(p)
        hoy = clock.now_local().date()
        for dia in (hoy, hoy + timedelta(days=1)):
            for tipo in ("manana", "noche"):
                h, _ = iniciativa.hora(dia, tipo)
                t = clock.local_to_utc(h)
                if t > self.reloj.t:
                    candidatos.append(t)
        return max(self.reloj.t + timedelta(seconds=1), min(candidatos))

    async def avanzar(self, minutos: float = 0, hasta: datetime | None = None, sin_programador: bool = False) -> None:
        """Pasa el tiempo hasta `hasta` (o `minutos` más), parando en cada evento (avisos, saludos)
        para correr el programador y repartir el buzón. sin_programador: simula que el sistema
        estuvo apagado (nada corre en ese lapso)."""
        fin = hasta or self.reloj.t + timedelta(minutes=minutos)
        if sin_programador:
            self.reloj.t = fin
            return
        await self._ciclo()
        while self.reloj.t < fin:
            self.reloj.t = self._proximo_evento(fin)
            await self._ciclo()

    async def hasta_local(self, dia: int, hh: int, mm: int = 0, sin_programador: bool = False) -> None:
        """Avanza hasta el día `dia` (0 = el lunes inicial) a la hora local hh:mm."""
        base = clock.local_to_utc(datetime.combine((INICIO.astimezone(clock.LOCAL_TZ)).date() + timedelta(days=dia),
                                                   datetime.min.time().replace(hour=hh, minute=mm)))
        if base > self.reloj.t:
            await self.avanzar(hasta=base, sin_programador=sin_programador)
