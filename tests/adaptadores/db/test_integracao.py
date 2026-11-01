"""Casos de uso sobre o Postgres semeado: os mesmos cenários de `tests/modulos/test_integracao_csv.py`."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import URL

from emporio.adaptadores.db import criar_repositorios_postgres
from emporio.modulos.atendimento.aplicacao import FerramentasLoja, ServicoAtendimento
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import ServicoComercial
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from tests.adaptadores.db.repos import settings_postgres
from tests.modulos.fakes import RelogioFixo, resolver_config

# Reuso dos cenários: o pytest os coleta aqui e resolve a fixture `lojas` deste módulo (Postgres).
from tests.modulos.test_integracao_csv import (  # noqa: F401
    test_acessorio_no_catalogo_real,
    test_instrumento_com_caracteristica_de_acessorio_nao_e_fora_de_escopo,
    test_pedido_entregue_real_com_janelas,
    test_takamine_gd20_no_catalogo_real,
    test_violoes_ate_mil_reais_no_catalogo_real,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def lojas(banco_migrado: URL) -> Iterator[FerramentasLoja]:
    tenants = {"emporio": resolver_config("emporio")}
    repos = criar_repositorios_postgres(settings_postgres(banco_migrado), tenants)
    relogio = RelogioFixo(datetime(2026, 3, 10, 15, tzinfo=UTC))
    politicas = ServicoPoliticas(relogio, resolver_config)
    yield FerramentasLoja(
        catalogo=ServicoCatalogo(repos.catalogo, resolver_config),
        comercial=ServicoComercial(repos.catalogo, politicas),
        pedidos=ServicoPedidos(repos.pedidos, repos.clientes, repos.catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(repos.solicitacoes, relogio),
    )
    repos.fechar()
