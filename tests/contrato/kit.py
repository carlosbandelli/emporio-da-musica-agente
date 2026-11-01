"""Peças comuns da suíte de contrato: o "kit" que cada motor entrega e o espião da fachada de tools.

Um motor novo entra na suíte escrevendo só uma `FabricaMotor` (ver `README.md`). Tudo aqui é independente do motor.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from emporio.adaptadores.llm import LLMFalso
from emporio.configuracao.tenants import carregar_tenants
from emporio.modulos.atendimento.aplicacao import ResultadoTool
from emporio.portas import DefinicaoTool, MotorDeAtendimento, Papel, SessaoStore, TenantContext
from emporio.portas.tenant import ConfigTenant

RAIZ: Final = Path(__file__).resolve().parents[2]
DATA_REFERENCIA: Final = datetime(2026, 3, 10, 15, tzinfo=UTC)
"""Terça-feira 12:00 em Campo Grande: loja aberta e preços conhecidos (Takamine GD20 = R$ 2.199,00)."""
TENANT_A: Final = "emporio"
TENANT_B: Final = "loja-teste"
WHATSAPP_A: Final = "(67) 3321-4500"
WHATSAPP_B: Final = "(67) 99999-0002"
PERSONA_B: Final = "Dora"
NOME_LOJA_B: Final = "Loja Teste Sul"


class EspiaoTools:
    """Envolve a fachada de tools do motor: registra cada tool executada e pode simular um backend fora do ar.

    A fábrica do motor deve entregar ao motor este espião no lugar da fachada real (`FerramentasLoja`). Os motores
    que chamam tools por outro caminho (ex.: n8n via HTTP) fazem o espião ficar no lado servidor da chamada.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.chamadas: list[str] = []
        self.falha: BaseException | None = None
        """Se definida, toda execução de tool levanta esta exceção (backend fora do ar)."""

    def definicoes(self, papel: Papel) -> tuple[DefinicaoTool, ...]:
        """Repassa as definições de tools da fachada real."""
        definicoes: tuple[DefinicaoTool, ...] = self._real.definicoes(papel)
        return definicoes

    async def executar(
        self, ctx: TenantContext, nome: str, argumentos: Mapping[str, object], *, sessao_id: str | None = None
    ) -> ResultadoTool:
        """Registra o nome e executa a tool real, ou levanta `falha`."""
        self.chamadas.append(nome)
        if self.falha is not None:
            raise self.falha
        resultado: ResultadoTool = await self._real.executar(ctx, nome, argumentos, sessao_id=sessao_id)
        return resultado


@dataclass(slots=True)
class KitMotor:
    """O que uma fábrica entrega ao teste: o motor mais os controles para roteirizar e observar.

    - `motor`: a implementação sob teste, só pela porta `MotorDeAtendimento`.
    - `llm`: `LLMFalso` que o motor usa; os testes roteirizam (`llm.roteirizar(...)`) e leem `llm.requisicoes`.
    - `tools`: espião da fachada de tools (`tools.chamadas`, `tools.falha`).
    - `sessoes`: o `SessaoStore` do motor, se ele expuser um (verificações extras de I2); senão `None`.
    """

    motor: MotorDeAtendimento
    llm: LLMFalso
    tools: EspiaoTools
    sessoes: SessaoStore | None = None


FabricaMotor = Callable[[Mapping[str, ConfigTenant], Path], KitMotor]
"""`fabrica(tenants, dir_estado) -> KitMotor`.

`tenants` já traz o deadline e os demais ajustes do teste (o motor deve usá-los como estão); `dir_estado` é uma pasta
temporária para qualquer arquivo que o motor grave. A fábrica usa data de referência `DATA_REFERENCIA`, dados reais de
`data/` e nenhum acesso a rede.
"""


def tenant_emporio() -> ConfigTenant:
    """Configuração real do tenant `emporio`."""
    return carregar_tenants(RAIZ / "config" / "tenants.yaml")["emporio"]


def tenants_ab(dir_tmp: Path) -> Mapping[str, ConfigTenant]:
    """Carrega `{emporio, loja-teste}` de um `tenants.yaml` temporário: a loja B copia a A (mesmos dados, mesmo manual),
    mas com outro nome, persona e WhatsApp, para que qualquer vazamento de configuração apareça no texto."""
    bruto = yaml.safe_load((RAIZ / "config" / "tenants.yaml").read_text(encoding="utf-8"))
    loja_b = yaml.safe_load(yaml.safe_dump(bruto["tenants"][TENANT_A]))  # cópia profunda
    loja_b["nome"] = NOME_LOJA_B
    loja_b["persona"] = {"nome": PERSONA_B, "resumo": "Atendente de teste da loja B."}
    loja_b["contatos"]["whatsapp"] = WHATSAPP_B
    bruto["tenants"][TENANT_B] = loja_b
    caminho = dir_tmp / "tenants.yaml"
    caminho.write_text(yaml.safe_dump(bruto, allow_unicode=True), encoding="utf-8")
    return carregar_tenants(caminho)
