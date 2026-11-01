"""`CircuitoStoreMemoria`: circuit breaker por provedor (política da arquitetura §5.2)."""

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from emporio.portas.estado import EstadoCircuito


@dataclass(frozen=True, slots=True)
class PoliticaCircuito:
    """Limiares e tempos do breaker (padrões da arquitetura §5.2)."""

    falhas_consecutivas: int = 5
    taxa_falha: float = 0.5
    janela_s: float = 30.0
    min_chamadas_janela: int = 10
    abertura_transitoria_s: float = 30.0  # base do backoff transitório
    abertura_permanente_s: float = 600.0  # base do backoff permanente
    teto_transitoria_s: float = 300.0
    teto_permanente_s: float = 3600.0
    fator_backoff: float = 2.0


@dataclass(slots=True)
class _Provedor:
    estado: EstadoCircuito = EstadoCircuito.FECHADO
    consecutivas: int = 0
    janela: deque[tuple[float, bool]] = field(default_factory=deque)  # (instante, sucesso)
    reabre_em: float = 0.0
    sonda_em_andamento: bool = False
    sonda_expira_em: float = 0.0
    reaberturas: int = 0  # reaberturas consecutivas (sonda falhou); zera quando a sonda tem sucesso


class CircuitoStoreMemoria:
    """Implementa `CircuitoStore`.

    Meio-aberto permite uma única sonda: a primeira consulta de `estado()` após o tempo de abertura devolve
    `MEIO_ABERTO` e reserva a sonda; as demais devolvem `ABERTO` até a sonda reportar sucesso (fecha) ou falha
    (reabre). Se a sonda nunca reportar, uma nova é liberada após `abertura_transitoria_s`.

    O tempo de abertura cresce a cada reabertura consecutiva: `d = min(teto, base * fator**n)` com jitter
    "equal" (`d/2 + U(0, d/2)`), para que réplicas não sondem juntas. Sucesso da sonda zera `n`.
    """

    def __init__(
        self,
        politica: PoliticaCircuito | None = None,
        fonte_tempo: Callable[[], float] = time.monotonic,
        aleatorio: random.Random | None = None,
    ) -> None:
        self._politica = politica or PoliticaCircuito()
        self._agora = fonte_tempo
        self._aleatorio = aleatorio or random.Random()
        self._provedores: dict[str, _Provedor] = {}

    def _obter(self, provedor: str) -> _Provedor:
        return self._provedores.setdefault(provedor, _Provedor())

    async def estado(self, provedor: str) -> EstadoCircuito:
        """Estado atual; pode transitar de aberto para meio-aberto (reservando a sonda)."""
        p = self._obter(provedor)
        agora = self._agora()
        if p.estado is EstadoCircuito.ABERTO and agora >= p.reabre_em:
            p.estado = EstadoCircuito.MEIO_ABERTO
            p.sonda_em_andamento = False
        if p.estado is EstadoCircuito.MEIO_ABERTO:
            # Singleflight: não há `await` entre checar e reservar a sonda; num processo asyncio isso é
            # atômico e funciona como lock. A versão Redis (Fase 2) usará `SET NX PX` na chave da sonda.
            if p.sonda_em_andamento and agora < p.sonda_expira_em:
                return EstadoCircuito.ABERTO
            p.sonda_em_andamento = True
            p.sonda_expira_em = agora + self._politica.abertura_transitoria_s
            return EstadoCircuito.MEIO_ABERTO
        return p.estado

    async def registrar_sucesso(self, provedor: str) -> None:
        """Sucesso: fecha o circuito (se meio-aberto) e zera a contagem de falhas consecutivas."""
        p = self._obter(provedor)
        if p.estado is EstadoCircuito.ABERTO and self._agora() < p.reabre_em:
            return  # resposta tardia de chamada anterior à abertura: não fecha antes da hora
        if p.estado is not EstadoCircuito.FECHADO:
            self._fechar(p)
            return
        p.consecutivas = 0
        self._registrar_janela(p, sucesso=True)

    async def registrar_falha(self, provedor: str, *, permanente: bool) -> None:
        """Falha permanente abre na hora (10 min); transitória abre por limiar (30 s)."""
        p = self._obter(provedor)
        if permanente:
            self._abrir(p, permanente=True)
            return
        if p.estado is EstadoCircuito.MEIO_ABERTO:
            self._abrir(p, permanente=False)
            return
        if p.estado is EstadoCircuito.ABERTO:
            return
        p.consecutivas += 1
        self._registrar_janela(p, sucesso=False)
        falhas = sum(1 for _, ok in p.janela if not ok)
        total = len(p.janela)
        excedeu_taxa = total >= self._politica.min_chamadas_janela and falhas / total >= self._politica.taxa_falha
        if p.consecutivas >= self._politica.falhas_consecutivas or excedeu_taxa:
            self._abrir(p, permanente=False)

    def _registrar_janela(self, p: _Provedor, *, sucesso: bool) -> None:
        agora = self._agora()
        p.janela.append((agora, sucesso))
        while p.janela and agora - p.janela[0][0] > self._politica.janela_s:
            p.janela.popleft()

    def _duracao(self, n: int, *, permanente: bool) -> float:
        pol = self._politica
        base, teto = (
            (pol.abertura_permanente_s, pol.teto_permanente_s)
            if permanente
            else (pol.abertura_transitoria_s, pol.teto_transitoria_s)
        )
        d = min(teto, base * pol.fator_backoff**n)
        return d / 2 + self._aleatorio.uniform(0.0, d / 2)

    def _abrir(self, p: _Provedor, *, permanente: bool) -> None:
        if p.estado is EstadoCircuito.MEIO_ABERTO:
            p.reaberturas += 1  # a sonda falhou: reabertura consecutiva (a primeira abertura usa n=0)
        elif p.estado is EstadoCircuito.FECHADO:
            p.reaberturas = 0
        n = p.reaberturas
        p.estado = EstadoCircuito.ABERTO
        p.reabre_em = self._agora() + self._duracao(n, permanente=permanente)
        p.sonda_em_andamento = False
        p.consecutivas = 0
        p.janela.clear()

    def _fechar(self, p: _Provedor) -> None:
        p.estado = EstadoCircuito.FECHADO
        p.reaberturas = 0
        p.consecutivas = 0
        p.sonda_em_andamento = False
        p.janela.clear()
