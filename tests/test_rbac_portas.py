"""RBAC como dado puro (task 2.5): matriz papel × permissão da §8, `autorizar`, `papel_no_tenant`, config editável."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from emporio.configuracao import carregar_tenants
from emporio.portas import (
    PERMISSOES_POR_PAPEL,
    AlteracaoConfig,
    Decisao,
    Identidade,
    Papel,
    Permissao,
    aplicar_alteracao,
    autorizar,
    papel_no_tenant,
    permissoes,
)

RAIZ = Path(__file__).resolve().parents[1]
P = Permissao

MATRIZ_ESPERADA = {
    Papel.CLIENTE: {P.CHAT_USAR},
    Papel.ATENDENTE: {P.CHAT_USAR, P.SOLICITACOES_LER, P.SOLICITACOES_RESOLVER, P.TOOLS_USAR},
    Papel.GERENTE: {P.CHAT_USAR, P.SOLICITACOES_LER, P.SOLICITACOES_RESOLVER, P.TOOLS_USAR, P.CONFIG_EDITAR},
    Papel.ADMIN_PLATAFORMA: {P.TENANTS_CRIAR, P.METRICAS_LER},
    Papel.SERVICO_TOOLS: {P.TOOLS_USAR},
}


def _id(*papeis: Papel, tenant: str = "emporio") -> Identidade:
    return Identidade(sub="u1", tenant_id=tenant, papeis=frozenset(papeis))


def test_matriz_cobre_os_cinco_papeis_e_e_a_da_arquitetura() -> None:
    assert set(PERMISSOES_POR_PAPEL) == set(Papel)
    assert {papel: set(ps) for papel, ps in PERMISSOES_POR_PAPEL.items()} == MATRIZ_ESPERADA


def test_gerente_tem_tudo_do_atendente() -> None:
    assert PERMISSOES_POR_PAPEL[Papel.ATENDENTE] < PERMISSOES_POR_PAPEL[Papel.GERENTE]


def test_matriz_e_imutavel() -> None:
    with pytest.raises(TypeError):
        PERMISSOES_POR_PAPEL[Papel.CLIENTE] = frozenset(Permissao)  # type: ignore[index]


@pytest.mark.parametrize("papel", list(Papel))
@pytest.mark.parametrize("permissao", list(Permissao))
def test_autorizar_segue_a_matriz_no_mesmo_tenant(papel: Papel, permissao: Permissao) -> None:
    esperado = Decisao.PERMITIDO if permissao in MATRIZ_ESPERADA[papel] else Decisao.SEM_PERMISSAO
    assert autorizar(_id(papel), permissao, "emporio") is esperado


@pytest.mark.parametrize("permissao", [P.CHAT_USAR, P.SOLICITACOES_LER, P.SOLICITACOES_RESOLVER, P.TOOLS_USAR])
def test_permissao_de_tenant_exige_o_mesmo_tenant(permissao: Permissao) -> None:
    assert autorizar(_id(Papel.GERENTE, tenant="outra"), permissao, "emporio") is Decisao.TENANT_DIVERGENTE


def test_permissao_de_plataforma_dispensa_o_tenant() -> None:
    assert autorizar(_id(Papel.ADMIN_PLATAFORMA, tenant="outra"), P.TENANTS_CRIAR, "emporio") is Decisao.PERMITIDO


def test_admin_plataforma_nao_le_solicitacoes_de_loja_nem_em_outro_tenant() -> None:
    assert autorizar(_id(Papel.ADMIN_PLATAFORMA), P.SOLICITACOES_LER, "emporio") is Decisao.SEM_PERMISSAO
    assert autorizar(_id(Papel.ADMIN_PLATAFORMA, tenant="x"), P.CONFIG_EDITAR, "emporio") is Decisao.SEM_PERMISSAO


def test_servico_tools_so_tem_tools_usar_e_nenhum_dado_pessoal() -> None:
    assert permissoes([Papel.SERVICO_TOOLS]) == {P.TOOLS_USAR}
    assert autorizar(_id(Papel.SERVICO_TOOLS), P.SOLICITACOES_LER, "emporio") is Decisao.SEM_PERMISSAO
    assert autorizar(_id(Papel.SERVICO_TOOLS, tenant="x"), P.TOOLS_USAR, "emporio") is Decisao.TENANT_DIVERGENTE


def test_sem_identidade_nao_autenticado() -> None:
    assert autorizar(None, P.CHAT_USAR, "emporio") is Decisao.NAO_AUTENTICADO


def test_varios_papeis_somam_permissoes() -> None:
    assert permissoes([Papel.CLIENTE, Papel.ADMIN_PLATAFORMA]) == {P.CHAT_USAR, P.TENANTS_CRIAR, P.METRICAS_LER}


@pytest.mark.parametrize(
    ("papeis", "esperado"),
    [
        ((), Papel.CLIENTE),
        ((Papel.ATENDENTE,), Papel.ATENDENTE),
        ((Papel.ATENDENTE, Papel.GERENTE), Papel.GERENTE),
        ((Papel.ADMIN_PLATAFORMA,), Papel.CLIENTE),
        ((Papel.ADMIN_PLATAFORMA, Papel.ATENDENTE), Papel.ATENDENTE),
        ((Papel.SERVICO_TOOLS,), Papel.SERVICO_TOOLS),
    ],
)
def test_papel_no_tenant(papeis: tuple[Papel, ...], esperado: Papel) -> None:
    assert papel_no_tenant(papeis) is esperado


# --- config editável -------------------------------------------------------------------------------------------------
def _config():  # type: ignore[no-untyped-def]
    return carregar_tenants(RAIZ / "config" / "tenants.yaml")["emporio"]


def test_alteracao_parcial_mantem_o_resto_e_revalida() -> None:
    atual = _config()
    nova = aplicar_alteracao(
        atual, AlteracaoConfig.model_validate({"persona": {"nome": "Bia", "resumo": "Atendente da loja."}})
    )
    assert nova.persona.nome == "Bia"
    assert nova.schema_db == atual.schema_db and nova.limites == atual.limites
    assert nova.termos_fora_de_escopo == atual.termos_fora_de_escopo


def test_alteracao_de_termos_com_nomes_de_idioma() -> None:
    nova = aplicar_alteracao(
        _config(), AlteracaoConfig.model_validate({"termos_fora_de_escopo": {"pt-BR": ["palheta"], "en-US": ["pick"]}})
    )
    assert nova.termos_fora_de_escopo.pt_br == ("palheta",)


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"schema": "outra"},
        {"dados_dir": "../../etc"},
        {"politicas_arquivo": "/etc/passwd"},
        {"persona": {"nome": "", "resumo": "x"}},
        {"persona": {"nome": "x" * 61, "resumo": "x"}},
        {"persona": {"nome": "Bia", "resumo": "x" * 2001}},
        {"deadline_mensagem_s": 0},
        {"deadline_mensagem_s": 500},
        {"termos_fora_de_escopo": {"pt-BR": [""], "en-US": []}},
        {"limites": {"sessao": {"capacidade": 0, "reposicao_por_s": 1}}},
    ],
)
def test_alteracao_invalida_ou_fora_do_editavel_e_recusada(corpo: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AlteracaoConfig.model_validate(corpo)
