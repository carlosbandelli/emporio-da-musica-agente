"""Composition root (arquitetura §3.3): o único lugar que instancia adapters e liga porta ↔ adapter.

`montar(settings)` lê a configuração dos tenants e o manual de políticas, cria os adapters de dados (CSV, JSONL),
de estado (memória), o relógio, os provedores de LLM (ou usa os injetados), o roteador, os casos de uso com a
fachada de tools e o motor escolhido por `MOTOR_ATENDIMENTO`. Tudo é criado por chamada: nada de singleton, `global`
ou cache em variável de módulo.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from emporio.adaptadores.csv import CatalogoCsv, ClientesCsv, PedidosCsv, SolicitacoesJsonl
from emporio.adaptadores.csv._leitura import ler_pedidos, ler_produtos
from emporio.adaptadores.db import ErroConfiguracaoBanco, RepositoriosPostgres, criar_repositorios_postgres
from emporio.adaptadores.llm import criar_provedores
from emporio.adaptadores.memoria import CircuitoStoreMemoria, RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.adaptadores.motor.nativo.prompt import carregar_politicas
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.adaptadores.observabilidade import criar_telemetria
from emporio.adaptadores.redis import criar_estado_redis
from emporio.adaptadores.relogio import RelogioSistema
from emporio.configuracao import ErroConfiguracao, Settings, carregar_tenants, obter_tenant
from emporio.modulos.atendimento.aplicacao import FerramentasLoja, ServicoAtendimento
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import ServicoComercial
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import (
    Catalogo,
    Clientes,
    DiagnosticoInfo,
    LLMProvider,
    MotorDeAtendimento,
    Pedidos,
    RateLimiter,
    Relogio,
    SessaoStore,
    Solicitacoes,
    Telemetria,
    TelemetriaNula,
    TenantContext,
)
from emporio.portas.tenant import ConfigTenant

MOTORES_DISPONIVEIS: Final = ("nativo",)
"""Valores aceitos de `MOTOR_ATENDIMENTO` nesta fase."""
MOTORES_FASE_2: Final = ("n8n",)
"""Valores previstos, ainda não implementados."""

_VARIAVEIS_CHAVE: Final = "GROQ_API_KEY, GOOGLE_API_KEY ou ANTHROPIC_API_KEY"

__all__ = [
    "MOTORES_DISPONIVEIS",
    "MOTORES_FASE_2",
    "Aplicacao",
    "DiagnosticoDetalhado",
    "DiagnosticoLocal",
    "ErroConfiguracao",
    "ErroSemChaveLLM",
    "Settings",
    "carregar_settings",
    "iniciar_observabilidade",
    "montar",
    "montar_diagnostico",
    "montar_ferramentas",
]


class ErroSemChaveLLM(ErroConfiguracao):
    """Nenhum provedor de LLM com chave (premissa #19): a CLI encerra com código 2 e esta mensagem."""


@dataclass(frozen=True, slots=True)
class Aplicacao:
    """O que os adapters de entrada recebem. Sem segredos: só nomes de provedores, nunca chaves."""

    motor: MotorDeAtendimento
    motor_ativo: str
    tenants: Mapping[str, ConfigTenant]
    tenant_padrao: str
    provedores: tuple[str, ...]
    """Nomes dos provedores de LLM em uso (com chave, ou os injetados)."""
    data_referencia: datetime | None = None
    telemetria: Telemetria = field(default_factory=TelemetriaNula)
    """Métricas e traces (nula por padrão; `iniciar_observabilidade` cria a real)."""


def iniciar_observabilidade(settings: Settings) -> Telemetria:
    """Liga a observabilidade real: logs JSON com scrubbing de PII, métricas Prometheus e traces OpenTelemetry.

    Traces só são exportados (OTLP HTTP) se `OTEL_EXPORTER_OTLP_ENDPOINT` existir. Quem chama passa o resultado
    a `montar(..., telemetria=...)`. Nada é ligado por `montar` sozinho (testes e CLI ficam com a telemetria nula).
    """
    return criar_telemetria(settings)


def carregar_settings(**valores: object) -> Settings:
    """Lê `Settings` do ambiente; valor inválido vira `ErroConfiguracao` com mensagem clara (sem stack trace).

    `valores` são repassados ao construtor (ex.: `_env_file=None` nos testes).
    """
    try:
        return Settings(**valores)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ErroConfiguracao(_explicar_settings(exc)) from None


def _explicar_settings(exc: ValidationError) -> str:
    problemas: list[str] = []
    for erro in exc.errors():
        campo = ".".join(str(p) for p in erro["loc"])
        if campo == "motor_atendimento":
            problemas.append(_mensagem_motor(str(erro.get("input"))))
        else:
            problemas.append(f"{campo.upper()}: {erro['msg']}")
    return "Configuração inválida: " + "; ".join(problemas)


def _mensagem_motor(valor: str) -> str:
    if valor in MOTORES_FASE_2:
        return f"MOTOR_ATENDIMENTO='{valor}' estará disponível na Fase 2; use 'nativo'."
    aceitos = ", ".join(MOTORES_DISPONIVEIS)
    return f"MOTOR_ATENDIMENTO='{valor}' é desconhecido. Valores aceitos: {aceitos}."


def montar(
    settings: Settings,
    *,
    provedores: Mapping[str, LLMProvider] | None = None,
    relogio: Relogio | None = None,
    raiz: Path | None = None,
    dir_estado: Path | None = None,
    relogio_monotonic: Callable[[], float] = time.monotonic,
    telemetria: Telemetria | None = None,
) -> Aplicacao:
    """Monta a aplicação. Levanta `ErroConfiguracao` (ou `ErroSemChaveLLM`) com mensagem clara.

    - `provedores`: injeta LLMs prontos (testes e CLI com `LLMFalso`); sem injeção, cria um `ProvedorResiliente` por
      provedor com chave em `settings`.
    - `raiz`: base dos caminhos relativos (`tenants_config`, `politicas_arquivo`, `dados_dir`); padrão: diretório
      atual (a raiz do repositório, ou `/app` no container).
    - `telemetria`: porta de métricas/traces injetada nos adapters (padrão: nula).
    - `dir_estado`: onde o modo local grava as solicitações (`<raiz>/var` por padrão).
    """
    motor_ativo = settings.motor_atendimento
    if motor_ativo not in MOTORES_DISPONIVEIS:
        raise ErroConfiguracao(_mensagem_motor(motor_ativo))

    telem: Telemetria = telemetria if telemetria is not None else TelemetriaNula()
    base = raiz if raiz is not None else Path.cwd()
    tenants = carregar_tenants(_resolver(base, settings.tenants_config))
    obter_tenant(tenants, settings.tenant_padrao)  # falha cedo com a lista de tenants disponíveis
    politicas = {tid: carregar_politicas(_resolver(base, c.politicas_arquivo)) for tid, c in tenants.items()}

    relogio_app: Relogio = relogio if relogio is not None else RelogioSistema(settings.data_referencia)
    if provedores is None:
        provedores = criar_provedores(
            settings, CircuitoStoreMemoria(), relogio_monotonic=relogio_monotonic, telemetria=telem
        )
    if not provedores:
        raise ErroSemChaveLLM(
            "Nenhuma chave de LLM configurada. Defina ao menos uma das variáveis "
            f"{_VARIAVEIS_CHAVE} no arquivo de ambiente do projeto (copie o exemplo da raiz) e rode "
            "`emporio diagnostico` para conferir."
        )

    # Estado: Redis quando REDIS_URL existe (Fase 2, fail-open); senão memória do processo (modo local, premissa #21).
    sessoes: SessaoStore
    limitador: RateLimiter
    envolver_catalogo: Callable[[Catalogo], Catalogo] | None = None
    if settings.redis_url is not None:
        estado = criar_estado_redis(settings, tenants)
        sessoes, limitador, envolver_catalogo = estado.sessoes, estado.limitador, estado.cache_catalogo
    else:
        sessoes, limitador = SessaoStoreMemoria(), RateLimiterMemoria(tenants)

    motor = AgenteNativo(
        tenants=tenants,
        politicas=politicas,
        roteador=Roteador(provedores, relogio_monotonic=relogio_monotonic, telemetria=telem),
        ferramentas=montar_ferramentas(
            tenants,
            raiz=base,
            relogio=relogio_app,
            dir_estado=dir_estado if dir_estado is not None else base / "var",
            envolver_catalogo=envolver_catalogo,
            repositorios=_repositorios_postgres(settings, tenants),
        ),
        sessoes=sessoes,
        limitador=limitador,
        relogio=relogio_app,
        relogio_monotonic=relogio_monotonic,
        telemetria=telem,
    )
    return Aplicacao(
        motor=motor,
        motor_ativo=motor_ativo,
        tenants=tenants,
        tenant_padrao=settings.tenant_padrao,
        provedores=tuple(provedores),
        data_referencia=settings.data_referencia,
        telemetria=telem,
    )


def montar_ferramentas(
    tenants: Mapping[str, ConfigTenant],
    *,
    raiz: Path,
    relogio: Relogio,
    dir_estado: Path,
    envolver_catalogo: Callable[[Catalogo], Catalogo] | None = None,
    repositorios: RepositoriosPostgres | None = None,
) -> FerramentasLoja:
    """Casos de uso sobre os dados do tenant reunidos na fachada de tools.

    Dados: Postgres (schema por tenant) quando `repositorios` é dado; senão CSVs + solicitações em JSONL (modo local).
    `envolver_catalogo`: decorador opcional do catálogo (ex.: cache-aside no Redis, que nunca guarda estoque).
    """

    def resolver_config(tenant_id: str) -> ConfigTenant:
        return obter_tenant(tenants, tenant_id)

    catalogo: Catalogo
    pedidos: Pedidos
    clientes: Clientes
    solicitacoes: Solicitacoes
    if repositorios is not None:
        catalogo, pedidos = repositorios.catalogo, repositorios.pedidos
        clientes, solicitacoes = repositorios.clientes, repositorios.solicitacoes
    else:
        catalogo, pedidos = CatalogoCsv(tenants, raiz), PedidosCsv(tenants, raiz)
        clientes, solicitacoes = ClientesCsv(tenants, raiz), SolicitacoesJsonl(dir_estado)
    if envolver_catalogo is not None:
        catalogo = envolver_catalogo(catalogo)
    politicas = ServicoPoliticas(relogio, resolver_config)
    return FerramentasLoja(
        catalogo=ServicoCatalogo(catalogo, resolver_config),
        comercial=ServicoComercial(catalogo, politicas),
        pedidos=ServicoPedidos(pedidos, clientes, catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(solicitacoes, relogio),
    )


def _repositorios_postgres(settings: Settings, tenants: Mapping[str, ConfigTenant]) -> RepositoriosPostgres | None:
    """Postgres quando `DATABASE_URL` existe (Fase 2; migrar antes com `python -m emporio.adaptadores.db.migrar`)."""
    if settings.database_url is None:
        return None
    try:
        return criar_repositorios_postgres(settings, tenants)
    except ErroConfiguracaoBanco as erro:
        raise ErroConfiguracao(str(erro)) from erro


def _resolver(base: Path, caminho: Path | str) -> Path:
    caminho = Path(caminho)
    return caminho if caminho.is_absolute() else base / caminho


@dataclass(frozen=True, slots=True)
class DiagnosticoDetalhado(DiagnosticoInfo):
    """`DiagnosticoInfo` com os detalhes que a CLI mostra (a porta traz só o núcleo). Sem segredos."""

    tenant_valido: bool = False
    n_produtos: int | None = None
    n_pedidos: int | None = None
    manual_encontrado: bool = False


class DiagnosticoLocal:
    """Implementa a porta `Diagnostico` sem exigir provedor de LLM nem montar o motor.

    Decisão: caminho separado (`montar_diagnostico`) em vez de tornar `Aplicacao.motor` opcional, para a API da
    `montar()` seguir garantindo motor funcional (sem `None` para os adapters de entrada tratarem) e para o
    diagnóstico funcionar justamente quando falta configuração (sem chave, tenant inválido). Nunca lança: problemas
    viram `avisos`.
    """

    def __init__(
        self, settings: Settings, *, provedores: tuple[str, ...] | None = None, raiz: Path | None = None
    ) -> None:
        self._settings = settings
        self._provedores = provedores
        self._raiz = raiz if raiz is not None else Path.cwd()
        self.tenant_padrao = settings.tenant_padrao

    async def diagnosticar(self, ctx: TenantContext) -> DiagnosticoDetalhado:
        """Verifica provedores, motor, tenant, dados e manual de políticas; devolve também os avisos."""
        s = self._settings
        avisos: list[str] = []
        provedores = self._provedores if self._provedores is not None else s.provedores_com_chave()
        if not provedores:
            avisos.append(
                f"Nenhuma chave de LLM configurada. Defina ao menos uma das variáveis {_VARIAVEIS_CHAVE} "
                "no arquivo de ambiente do projeto (copie o exemplo da raiz) ou no ambiente do shell."
            )
        if s.motor_atendimento not in MOTORES_DISPONIVEIS:
            avisos.append(_mensagem_motor(s.motor_atendimento))

        tenant_valido = False
        n_produtos: int | None = None
        n_pedidos: int | None = None
        manual = False
        try:
            tenants = carregar_tenants(_resolver(self._raiz, s.tenants_config))
            config = tenants.get(ctx.tenant_id)
            if config is None:
                avisos.append(f"Tenant '{ctx.tenant_id}' não existe. Disponíveis: {', '.join(sorted(tenants))}.")
            else:
                tenant_valido = True
                manual = self._manual_existe(config.politicas_arquivo, avisos)
                n_produtos, n_pedidos = self._contar_dados(config.dados_dir, avisos)
        except ErroConfiguracao as exc:
            avisos.append(str(exc))
        if s.data_referencia is None:
            avisos.append("DATA_REFERENCIA não definida: o agente usa a data real (pedidos do CSV vão até 2026-03).")

        return DiagnosticoDetalhado(
            provedores_com_chave=tuple(provedores),
            motor_ativo=s.motor_atendimento,
            tenant_id=ctx.tenant_id,
            dados_carregados=n_produtos is not None and n_pedidos is not None,
            data_referencia=s.data_referencia,
            avisos=tuple(avisos),
            tenant_valido=tenant_valido,
            n_produtos=n_produtos,
            n_pedidos=n_pedidos,
            manual_encontrado=manual,
        )

    def _manual_existe(self, arquivo: str, avisos: list[str]) -> bool:
        try:
            carregar_politicas(_resolver(self._raiz, arquivo))
        except ErroConfiguracao as exc:
            avisos.append(str(exc))
            return False
        return True

    def _contar_dados(self, dados_dir: str, avisos: list[str]) -> tuple[int | None, int | None]:
        try:
            pasta = _resolver(self._raiz, dados_dir)
            return len(ler_produtos(pasta)), len(ler_pedidos(pasta))
        except Exception as exc:
            avisos.append(f"Não foi possível carregar os dados em '{dados_dir}': {exc}")
            return None, None


def montar_diagnostico(
    settings: Settings, *, provedores: tuple[str, ...] | None = None, raiz: Path | None = None
) -> DiagnosticoLocal:
    """Diagnóstico que funciona sem nenhum provedor de LLM. `provedores` injeta nomes (testes)."""
    return DiagnosticoLocal(settings, provedores=provedores, raiz=raiz)
