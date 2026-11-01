"""Composition root (arquitetura §3.3): o único lugar que instancia adapters e liga porta ↔ adapter.

`montar(settings)` lê a configuração dos tenants e o manual de políticas, cria os adapters de dados (CSV, JSONL),
de estado (memória), o relógio, os provedores de LLM (ou usa os injetados), o roteador, os casos de uso com a
fachada de tools e o motor escolhido por `MOTOR_ATENDIMENTO`. Tudo é criado por chamada: nada de singleton, `global`
ou cache em variável de módulo.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from emporio.adaptadores.csv import CatalogoCsv, ClientesCsv, PedidosCsv, SolicitacoesJsonl
from emporio.adaptadores.llm import criar_provedores
from emporio.adaptadores.memoria import CircuitoStoreMemoria, RateLimiterMemoria, SessaoStoreMemoria
from emporio.adaptadores.motor.nativo import AgenteNativo
from emporio.adaptadores.motor.nativo.prompt import carregar_politicas
from emporio.adaptadores.motor.nativo.roteador import Roteador
from emporio.adaptadores.relogio import RelogioSistema
from emporio.configuracao import ErroConfiguracao, Settings, carregar_tenants, obter_tenant
from emporio.modulos.atendimento.aplicacao import FerramentasLoja, ServicoAtendimento
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import ServicoComercial
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import LLMProvider, MotorDeAtendimento, Relogio
from emporio.portas.tenant import ConfigTenant

MOTORES_DISPONIVEIS: Final = ("nativo",)
"""Valores aceitos de `MOTOR_ATENDIMENTO` nesta fase."""
MOTORES_FASE_2: Final = ("n8n",)
"""Valores previstos, ainda não implementados."""

_VARIAVEIS_CHAVE: Final = "GROQ_API_KEY, GOOGLE_API_KEY ou ANTHROPIC_API_KEY"


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
) -> Aplicacao:
    """Monta a aplicação. Levanta `ErroConfiguracao` (ou `ErroSemChaveLLM`) com mensagem clara.

    - `provedores`: injeta LLMs prontos (testes e CLI com `LLMFalso`); sem injeção, cria um `ProvedorResiliente` por
      provedor com chave em `settings`.
    - `raiz`: base dos caminhos relativos (`tenants_config`, `politicas_arquivo`, `dados_dir`); padrão: diretório
      atual (a raiz do repositório, ou `/app` no container).
    - `dir_estado`: onde o modo local grava as solicitações (`<raiz>/var` por padrão).
    """
    motor_ativo = settings.motor_atendimento
    if motor_ativo not in MOTORES_DISPONIVEIS:
        raise ErroConfiguracao(_mensagem_motor(motor_ativo))

    base = raiz if raiz is not None else Path.cwd()
    tenants = carregar_tenants(_resolver(base, settings.tenants_config))
    obter_tenant(tenants, settings.tenant_padrao)  # falha cedo com a lista de tenants disponíveis
    politicas = {tid: carregar_politicas(_resolver(base, c.politicas_arquivo)) for tid, c in tenants.items()}

    relogio_app: Relogio = relogio if relogio is not None else RelogioSistema(settings.data_referencia)
    if provedores is None:
        provedores = criar_provedores(settings, CircuitoStoreMemoria(), relogio_monotonic=relogio_monotonic)
    if not provedores:
        raise ErroSemChaveLLM(
            "Nenhuma chave de LLM configurada. Defina ao menos uma das variáveis "
            f"{_VARIAVEIS_CHAVE} no arquivo de ambiente do projeto (copie o exemplo da raiz) e rode "
            "`emporio diagnostico` para conferir."
        )

    motor = AgenteNativo(
        tenants=tenants,
        politicas=politicas,
        roteador=Roteador(provedores, relogio_monotonic=relogio_monotonic),
        ferramentas=montar_ferramentas(
            tenants, raiz=base, relogio=relogio_app, dir_estado=dir_estado if dir_estado is not None else base / "var"
        ),
        sessoes=SessaoStoreMemoria(),
        limitador=RateLimiterMemoria(tenants),
        relogio=relogio_app,
        relogio_monotonic=relogio_monotonic,
    )
    return Aplicacao(
        motor=motor,
        motor_ativo=motor_ativo,
        tenants=tenants,
        tenant_padrao=settings.tenant_padrao,
        provedores=tuple(provedores),
        data_referencia=settings.data_referencia,
    )


def montar_ferramentas(
    tenants: Mapping[str, ConfigTenant], *, raiz: Path, relogio: Relogio, dir_estado: Path
) -> FerramentasLoja:
    """Casos de uso sobre os CSVs do tenant + solicitações em JSONL, reunidos na fachada de tools."""

    def resolver_config(tenant_id: str) -> ConfigTenant:
        return obter_tenant(tenants, tenant_id)

    catalogo = CatalogoCsv(tenants, raiz)
    politicas = ServicoPoliticas(relogio, resolver_config)
    return FerramentasLoja(
        catalogo=ServicoCatalogo(catalogo, resolver_config),
        comercial=ServicoComercial(catalogo, politicas),
        pedidos=ServicoPedidos(PedidosCsv(tenants, raiz), ClientesCsv(tenants, raiz), catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(SolicitacoesJsonl(dir_estado), relogio),
    )


def _resolver(base: Path, caminho: Path | str) -> Path:
    caminho = Path(caminho)
    return caminho if caminho.is_absolute() else base / caminho
