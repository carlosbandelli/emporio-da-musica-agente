"""`ProvedorResiliente`: deadline → bulkhead → timeout → retry → circuit breaker (arquitetura §5.2).

O fallback entre provedores é do roteador (task 1.9): aqui cada provedor falha rápido e com um erro que diz se vale
trocar de provedor (`ErroLLMTransitorio`, `ErroCircuitoAberto`) ou não (`ErroPrazoEsgotado`, `ErroLLMPermanente`).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, wait_random_exponential

from emporio.portas.estado import CircuitoStore, EstadoCircuito
from emporio.portas.llm import (
    ErroCircuitoAberto,
    ErroLLMAutenticacao,
    ErroLLMPermanente,
    ErroLLMTransitorio,
    ErroPrazoEsgotado,
    LLMProvider,
    RequisicaoLLM,
    RespostaLLM,
)

Sono = Callable[[float], Awaitable[None]]
RelogioMonotonic = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PoliticaResiliencia:
    """Valores da arquitetura §5.2. `max_concorrentes` é o bulkhead (por instância do provedor)."""

    max_concorrentes: int
    tentativas: int = 3
    espera_multiplicador: float = 0.5
    espera_max_s: float = 4.0
    retry_after_max_s: float = 5.0


def _retentavel(erro: BaseException) -> bool:
    return isinstance(erro, ErroLLMTransitorio) and not isinstance(erro, ErroCircuitoAberto | ErroPrazoEsgotado)


class ProvedorResiliente:
    """Embrulha um `LLMProvider` e também satisfaz `LLMProvider`.

    Por tentativa: confere o deadline, consulta o circuito (aberto → `ErroCircuitoAberto` sem chamar a API), entra
    no bulkhead (`asyncio.Semaphore` desta instância) e chama o provedor com timeout `min(req.timeout_s, restante)`.
    Resultado no circuito: sucesso → `registrar_sucesso`; transitório/timeout → falha transitória; autenticação →
    falha permanente (abre na hora); erro de requisição (400, recusa) → `registrar_sucesso`, porque o provedor
    respondeu e está saudável (o problema é o pedido). Timeout causado pelo deadline vira `ErroPrazoEsgotado` e não
    conta contra o provedor.

    Retry (`tenacity`): só transitórios, até `tentativas`; espera = `Retry-After` quando vier, senão
    `wait_random_exponential`. Desiste na hora se `Retry-After` > `retry_after_max_s` ou se, depois da espera, o
    tempo restante do deadline for menor que `req.timeout_s`.
    """

    def __init__(
        self,
        interno: LLMProvider,
        circuito: CircuitoStore,
        politica: PoliticaResiliencia,
        *,
        sleep: Sono = asyncio.sleep,
        relogio_monotonic: RelogioMonotonic = time.monotonic,
    ) -> None:
        self.interno = interno
        self.politica = politica
        self._circuito = circuito
        self._sleep = sleep
        self._agora = relogio_monotonic
        self._bulkhead = asyncio.Semaphore(politica.max_concorrentes)
        self._espera_base = wait_random_exponential(multiplier=politica.espera_multiplicador, max=politica.espera_max_s)

    @property
    def nome(self) -> str:
        """Nome do provedor embrulhado."""
        return self.interno.nome

    async def gerar(self, req: RequisicaoLLM) -> RespostaLLM:
        """Gera com as proteções da §5.2; erros seguem a hierarquia da porta."""
        tentativas = AsyncRetrying(
            stop=lambda estado: self._parar(estado, req),
            wait=self._esperar,
            retry=retry_if_exception(_retentavel),
            sleep=self._sleep,
            reraise=True,
        )
        return await tentativas(self._tentativa, req)

    def _restante(self, req: RequisicaoLLM) -> float | None:
        return None if req.prazo_monotonic is None else req.prazo_monotonic - self._agora()

    def _esperar(self, estado: RetryCallState) -> float:
        erro = estado.outcome.exception() if estado.outcome else None
        if isinstance(erro, ErroLLMTransitorio) and erro.retry_after_s is not None:
            return erro.retry_after_s
        return float(self._espera_base(estado))

    def _parar(self, estado: RetryCallState, req: RequisicaoLLM) -> bool:
        if estado.attempt_number >= self.politica.tentativas:
            return True
        erro = estado.outcome.exception() if estado.outcome else None
        if (
            isinstance(erro, ErroLLMTransitorio)
            and erro.retry_after_s is not None
            and erro.retry_after_s > self.politica.retry_after_max_s
        ):
            return True
        restante = self._restante(req)
        return restante is not None and restante - estado.upcoming_sleep < req.timeout_s

    async def _tentativa(self, req: RequisicaoLLM) -> RespostaLLM:
        nome = self.nome
        restante = self._restante(req)
        if restante is not None and restante <= 0:
            raise ErroPrazoEsgotado(f"{nome}: prazo esgotado antes da chamada")
        if await self._circuito.estado(nome) is EstadoCircuito.ABERTO:
            raise ErroCircuitoAberto(f"{nome}: circuito aberto")
        try:
            await asyncio.wait_for(self._bulkhead.acquire(), timeout=restante)
        except TimeoutError:
            raise ErroPrazoEsgotado(f"{nome}: prazo esgotado esperando vaga no bulkhead") from None
        try:
            return await self._chamar(req, nome)
        finally:
            self._bulkhead.release()

    async def _chamar(self, req: RequisicaoLLM, nome: str) -> RespostaLLM:
        restante = self._restante(req)
        limitado_pelo_prazo = restante is not None and restante < req.timeout_s
        limite = min(req.timeout_s, restante) if restante is not None else req.timeout_s
        try:
            async with asyncio.timeout(max(limite, 0.0)):
                resposta = await self.interno.gerar(req)
        except TimeoutError:
            if limitado_pelo_prazo:
                raise ErroPrazoEsgotado(f"{nome}: prazo esgotado durante a chamada") from None
            await self._circuito.registrar_falha(nome, permanente=False)
            raise ErroLLMTransitorio(f"{nome}: timeout de {req.timeout_s:g} s") from None
        except ErroLLMAutenticacao:
            await self._circuito.registrar_falha(nome, permanente=True)
            raise
        except ErroLLMPermanente:
            await self._circuito.registrar_sucesso(nome)
            raise
        except ErroLLMTransitorio:
            await self._circuito.registrar_falha(nome, permanente=False)
            raise
        await self._circuito.registrar_sucesso(nome)
        return resposta
