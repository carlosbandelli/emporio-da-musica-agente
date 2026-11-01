"""Ponta a ponta com Postgres: `montar()` com DATABASE_URL usa os repositórios do schema do tenant."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import URL

from emporio.adaptadores.llm import LLMFalso, chamar_tool
from emporio.composicao import montar
from emporio.configuracao import ErroConfiguracao, Settings
from emporio.portas import MensagemCliente, PapelMensagem, RequisicaoLLM, RespostaLLM, TenantContext
from tests.adaptadores.db.conftest import texto_url
from tests.modulos.fakes import RelogioFixo

RAIZ = Path(__file__).resolve().parents[3]


@pytest.mark.postgres
async def test_chat_com_postgres_responde_com_os_dados_do_schema(banco_migrado: URL, tmp_path: Path) -> None:
    def responder(req: RequisicaoLLM) -> RespostaLLM:
        tool = [m for m in req.mensagens if m.role is PapelMensagem.TOOL][-1]
        assert '"R$ 2.199,00"' in tool.content
        return RespostaLLM("O Takamine GD20 sai por R$ 2.199,00.", "", "")

    app = montar(
        Settings(_env_file=None, database_url=texto_url(banco_migrado)),  # type: ignore[call-arg,arg-type]
        provedores={"falso": LLMFalso([chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}), responder])},
        relogio=RelogioFixo(datetime(2026, 3, 10, 15, tzinfo=UTC)),
        raiz=RAIZ,
        dir_estado=tmp_path,
    )
    r = await app.motor.responder(TenantContext("emporio"), "s1", MensagemCliente("quanto custa o Takamine GD20?"))
    assert r.degradada is False and "R$ 2.199,00" in r.texto and r.tools_chamadas == ("detalhar_produto",)


def test_database_url_invalida_e_erro_de_configuracao(tmp_path: Path) -> None:
    with pytest.raises(ErroConfiguracao):
        montar(
            Settings(_env_file=None, database_url="mysql://x@y/z"),  # type: ignore[call-arg,arg-type]
            provedores={"falso": LLMFalso([])},
            raiz=RAIZ,
            dir_estado=tmp_path,
        )
