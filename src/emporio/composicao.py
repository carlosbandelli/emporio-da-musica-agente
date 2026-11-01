"""Composition root (arquitetura §3.3): o único lugar que instancia adapters e liga porta ↔ adapter.

`montar(settings)` lê a configuração dos tenants e o manual de políticas, cria os adapters de dados (CSV, JSONL),
de estado (memória), o relógio, os provedores de LLM (ou usa os injetados), o roteador, os casos de uso com a
fachada de tools e o motor escolhido por `MOTOR_ATENDIMENTO`. Tudo é criado por chamada: nada de singleton, `global`
ou cache em variável de módulo.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final

import httpx
from pydantic import ValidationError

from emporio.adaptadores.csv import CatalogoCsv, ClientesCsv, PedidosCsv, SolicitacoesJsonl
from emporio.adaptadores.csv._leitura import ler_pedidos, ler_produtos
from emporio.adaptadores.db import ErroConfiguracaoBanco, RepositoriosPostgres, criar_repositorios_postgres
from emporio.adaptadores.identidade import ErroSegredoJwt, JwtLocal
from emporio.adaptadores.llm import criar_provedores
from emporio.adaptadores.memoria import CircuitoStoreMemoria, RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.comum.prompt import carregar_politicas
from emporio.adaptadores.motor.n8n import AgenteN8n
from emporio.adaptadores.motor.nativo import AgenteNativo
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
    AlteracaoConfig,
    Catalogo,
    Clientes,
    ConfigTenants,
    DiagnosticoInfo,
    FachadaTools,
    LLMProvider,
    MotorDeAtendimento,
    Pedidos,
    ProvedorIdentidade,
    RateLimiter,
    Relogio,
    SessaoStore,
    Solicitacoes,
    Telemetria,
    TelemetriaNula,
    TenantContext,
    aplicar_alteracao,
)
from emporio.portas.tenant import ConfigTenant

logger = logging.getLogger(__name__)

MOTORES_DISPONIVEIS: Final = ("nativo", "n8n")
"""Valores aceitos de `MOTOR_ATENDIMENTO`."""
MOTORES_FASE_2: Final[tuple[str, ...]] = ()
"""Valores previstos, ainda não implementados (o `n8n` entrou na task 2.7)."""

_VARIAVEIS_CHAVE: Final = "GROQ_API_KEY, GOOGLE_API_KEY ou ANTHROPIC_API_KEY"

__all__ = [
    "MOTORES_DISPONIVEIS",
    "MOTORES_FASE_2",
    "Aplicacao",
    "ConfigTenantsMemoria",
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
    "montar_identidade",
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
    identidade: ProvedorIdentidade | None = None
    """Validação de tokens (RBAC). `None` = sem `JWT_SECRET` válido: endpoints protegidos respondem 503."""
    solicitacoes: Solicitacoes | None = None
    """Porta das solicitações para os endpoints admin (o mesmo repositório das tools)."""
    config_tenants: ConfigTenants | None = None
    """Configuração editável dos tenants (em memória do processo)."""
    ferramentas: FachadaTools | None = None
    """Fachada de tools (a mesma do motor nativo) para `/v1/tools/*` e motores externos como o n8n."""


class ConfigTenantsMemoria:
    """Implementa `ConfigTenants` em memória do processo (protótipo; persistir em arquivo/DB é melhoria futura).

    `mapa` é uma visão viva (somente leitura) do dicionário interno: quem a recebe na montagem (API, rate limiter,
    resolvedor de config dos casos de uso) passa a ver a edição sem remontar nada. Cada réplica tem a sua cópia.
    """

    def __init__(self, tenants: Mapping[str, ConfigTenant]) -> None:
        self._tenants: dict[str, ConfigTenant] = dict(tenants)
        self.mapa: Mapping[str, ConfigTenant] = MappingProxyType(self._tenants)

    async def obter(self, ctx: TenantContext) -> ConfigTenant:
        """Configuração vigente do tenant."""
        return obter_tenant(self._tenants, ctx.tenant_id)

    async def atualizar(self, ctx: TenantContext, alteracao: AlteracaoConfig) -> ConfigTenant:
        """Aplica a alteração (revalidando a config inteira) e troca a entrada do tenant de uma vez."""
        nova = aplicar_alteracao(obter_tenant(self._tenants, ctx.tenant_id), alteracao)
        self._tenants[ctx.tenant_id] = nova
        return nova


def montar_identidade(settings: Settings) -> ProvedorIdentidade:
    """`ProvedorIdentidade` a partir de `JWT_SECRET` (único lugar de onde o segredo vem). Sem segredo válido →
    `ErroConfiguracao` com mensagem clara (nunca mostra o valor)."""
    if settings.jwt_secret is None or not settings.jwt_secret.get_secret_value().strip():
        raise ErroConfiguracao(
            "JWT_SECRET não definido. Defina um segredo aleatório longo (ao menos 32 bytes) no ambiente para emitir "
            "e validar tokens."
        )
    try:
        return JwtLocal(settings.jwt_secret)
    except ErroSegredoJwt as erro:
        raise ErroConfiguracao(f"JWT_SECRET inválido: {erro}.") from None


def _identidade_opcional(settings: Settings) -> ProvedorIdentidade | None:
    """Na API a identidade é opcional: sem segredo, o chat segue público e só os endpoints protegidos ficam 503."""
    try:
        return montar_identidade(settings)
    except ErroConfiguracao as erro:
        if settings.jwt_secret is not None:  # definido mas inválido: avisa alto (ausente é o modo local normal)
            logger.error("identidade desativada: %s", erro)
        return None


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
    if valor in MOTORES_FASE_2:  # pragma: no cover - nenhum motor pendente hoje
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

    Com `MOTOR_ATENDIMENTO=n8n` o motor é o `AgenteN8n` (o LLM fica no workflow): exige `N8N_WEBHOOK_URL` e
    `JWT_SECRET`, não exige chave de LLM e ignora `provedores`.
    """
    motor_ativo = settings.motor_atendimento
    if motor_ativo not in MOTORES_DISPONIVEIS:
        raise ErroConfiguracao(_mensagem_motor(motor_ativo))

    telem: Telemetria = telemetria if telemetria is not None else TelemetriaNula()
    base = raiz if raiz is not None else Path.cwd()
    config_tenants = ConfigTenantsMemoria(carregar_tenants(_resolver(base, settings.tenants_config)))
    tenants = config_tenants.mapa  # visão viva: edições de `PUT /v1/admin/config` aparecem para quem a recebe
    obter_tenant(tenants, settings.tenant_padrao)  # falha cedo com a lista de tenants disponíveis
    politicas = {tid: carregar_politicas(_resolver(base, c.politicas_arquivo)) for tid, c in tenants.items()}

    relogio_app: Relogio = relogio if relogio is not None else RelogioSistema(settings.data_referencia)
    n8n = _config_n8n(settings) if motor_ativo == "n8n" else None
    if n8n is not None:
        provedores = {}  # o LLM mora no workflow do n8n: o Python não exige chave
    elif provedores is None:
        provedores = criar_provedores(
            settings, CircuitoStoreMemoria(), relogio_monotonic=relogio_monotonic, telemetria=telem
        )
    if n8n is None and not provedores:
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

    repositorios = _repositorios_postgres(settings, tenants)
    estado_dir = dir_estado if dir_estado is not None else base / "var"
    solicitacoes: Solicitacoes = (
        repositorios.solicitacoes if repositorios is not None else SolicitacoesJsonl(estado_dir)
    )
    ferramentas = montar_ferramentas(
        tenants,
        raiz=base,
        relogio=relogio_app,
        dir_estado=estado_dir,
        envolver_catalogo=envolver_catalogo,
        repositorios=repositorios,
        solicitacoes=solicitacoes,
    )
    identidade = _identidade_opcional(settings) if n8n is None else n8n.identidade
    motor: MotorDeAtendimento
    if n8n is not None:
        motor = AgenteN8n(
            webhook_url=n8n.webhook_url,
            segredo_webhook=n8n.segredo,
            url_tools=settings.n8n_url_api,
            tenants=tenants,
            politicas=politicas,
            http=httpx.AsyncClient(),  # um cliente (pool de conexões) por aplicação montada
            identidade=n8n.identidade,
            circuito=CircuitoStoreMemoria(),
            sessoes=sessoes,
            limitador=limitador,
            relogio=relogio_app,
            relogio_monotonic=relogio_monotonic,
            telemetria=telem,
        )
    else:
        motor = AgenteNativo(
            tenants=tenants,
            politicas=politicas,
            roteador=Roteador(provedores, relogio_monotonic=relogio_monotonic, telemetria=telem),
            ferramentas=ferramentas,
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
        provedores=tuple(provedores) if n8n is None else ("n8n",),
        data_referencia=settings.data_referencia,
        telemetria=telem,
        identidade=identidade,
        solicitacoes=solicitacoes,
        config_tenants=config_tenants,
        ferramentas=ferramentas,
    )


def montar_ferramentas(
    tenants: Mapping[str, ConfigTenant],
    *,
    raiz: Path,
    relogio: Relogio,
    dir_estado: Path,
    envolver_catalogo: Callable[[Catalogo], Catalogo] | None = None,
    repositorios: RepositoriosPostgres | None = None,
    solicitacoes: Solicitacoes | None = None,
) -> FerramentasLoja:
    """Casos de uso sobre os dados do tenant reunidos na fachada de tools.

    Dados: Postgres (schema por tenant) quando `repositorios` é dado; senão CSVs + solicitações em JSONL (modo local).
    `envolver_catalogo`: decorador opcional do catálogo (ex.: cache-aside no Redis, que nunca guarda estoque).
    `solicitacoes`: repositório já criado (o mesmo que os endpoints admin usam); padrão: o dos dados escolhidos.
    """

    def resolver_config(tenant_id: str) -> ConfigTenant:
        return obter_tenant(tenants, tenant_id)

    catalogo: Catalogo
    pedidos: Pedidos
    clientes: Clientes
    repo_solicitacoes: Solicitacoes
    if repositorios is not None:
        catalogo, pedidos = repositorios.catalogo, repositorios.pedidos
        clientes, repo_solicitacoes = repositorios.clientes, repositorios.solicitacoes
    else:
        catalogo, pedidos = CatalogoCsv(tenants, raiz), PedidosCsv(tenants, raiz)
        clientes, repo_solicitacoes = ClientesCsv(tenants, raiz), SolicitacoesJsonl(dir_estado)
    if solicitacoes is not None:
        repo_solicitacoes = solicitacoes
    if envolver_catalogo is not None:
        catalogo = envolver_catalogo(catalogo)
    politicas = ServicoPoliticas(relogio, resolver_config)
    return FerramentasLoja(
        catalogo=ServicoCatalogo(catalogo, resolver_config),
        comercial=ServicoComercial(catalogo, politicas),
        pedidos=ServicoPedidos(pedidos, clientes, catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(repo_solicitacoes, relogio),
    )


@dataclass(frozen=True, slots=True)
class _ConfigN8n:
    webhook_url: str
    segredo: str | None
    identidade: ProvedorIdentidade


def _config_n8n(settings: Settings) -> _ConfigN8n:
    """Valida o que `MOTOR_ATENDIMENTO=n8n` exige, na inicialização: URL do webhook e `JWT_SECRET` (o motor emite o
    token de serviço com que o workflow chama `/v1/tools/*`). Nunca mostra valores."""
    url = settings.n8n_webhook_url.get_secret_value().strip() if settings.n8n_webhook_url else ""
    if not url:
        raise ErroConfiguracao(
            "MOTOR_ATENDIMENTO='n8n' exige N8N_WEBHOOK_URL (URL do Webhook do workflow; ver deploy/n8n/README.md)."
        )
    if not url.startswith(("http://", "https://")):
        raise ErroConfiguracao("N8N_WEBHOOK_URL deve começar com http:// ou https://.")
    try:
        identidade = montar_identidade(settings)
    except ErroConfiguracao as erro:
        raise ErroConfiguracao(f"MOTOR_ATENDIMENTO='n8n': {erro}") from None
    segredo = settings.n8n_webhook_segredo.get_secret_value().strip() if settings.n8n_webhook_segredo else ""
    if not segredo:
        logger.warning("motor n8n: N8N_WEBHOOK_SEGREDO não definido; o webhook fica sem autenticação")
    return _ConfigN8n(webhook_url=url, segredo=segredo or None, identidade=identidade)


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
        if s.motor_atendimento == "n8n":
            if s.n8n_webhook_url is None or not s.n8n_webhook_url.get_secret_value().strip():
                avisos.append("MOTOR_ATENDIMENTO='n8n' sem N8N_WEBHOOK_URL (ver deploy/n8n/README.md).")
            if s.jwt_secret is None:
                avisos.append("MOTOR_ATENDIMENTO='n8n' exige JWT_SECRET (token de serviço das tools).")
        elif not provedores:
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
