"""Registro de solicitações e fachada de tools."""

import json

from pydantic import BaseModel

from emporio.modulos.atendimento.aplicacao import FerramentasLoja, ServicoAtendimento, Tool
from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from emporio.modulos.comercial.aplicacao import ServicoComercial
from emporio.modulos.pedidos.aplicacao import ServicoPedidos
from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from emporio.portas import Papel, TenantContext
from tests.modulos.fakes import (
    CTX,
    CatalogoFalso,
    ClientesFalso,
    PedidosFalso,
    RelogioFixo,
    SolicitacoesFalso,
    local,
    resolver_config,
)

TOOLS_CLIENTE = {
    "buscar_produtos",
    "detalhar_produto",
    "simular_pagamento",
    "consultar_pedido",
    "status_loja",
    "registrar_solicitacao",
}


def montar() -> tuple[FerramentasLoja, SolicitacoesFalso]:
    relogio = RelogioFixo(local(2026, 3, 10))
    catalogo = CatalogoFalso()
    solicitacoes = SolicitacoesFalso()
    politicas = ServicoPoliticas(relogio, resolver_config)
    ferramentas = FerramentasLoja(
        catalogo=ServicoCatalogo(catalogo, resolver_config),
        comercial=ServicoComercial(catalogo, politicas),
        pedidos=ServicoPedidos(PedidosFalso(), ClientesFalso(), catalogo, relogio, politicas),
        politicas=politicas,
        atendimento=ServicoAtendimento(solicitacoes, relogio),
    )
    return ferramentas, solicitacoes


# --- registrar_solicitacao -------------------------------------------------------------------------------------------
async def test_idempotencia_mesma_chave_um_registro() -> None:
    _, repo = montar()
    servico = ServicoAtendimento(repo, RelogioFixo(local(2026, 3, 10)))
    a = await servico.registrar_solicitacao(
        CTX, tipo="reclamacao", descricao="Chegou riscado", sessao_id="s1", chave_idempotencia="k"
    )
    b = await servico.registrar_solicitacao(
        CTX, tipo="reclamacao", descricao="Outro texto", sessao_id="s1", chave_idempotencia="k"
    )
    assert a.protocolo == b.protocolo
    assert len(await repo.listar(CTX)) == 1


async def test_chave_derivada_ignora_caixa_e_espacos_mas_separa_sessao_e_tipo() -> None:
    _, repo = montar()
    servico = ServicoAtendimento(repo, RelogioFixo(local(2026, 3, 10)))
    base = await servico.registrar_solicitacao(CTX, tipo="reclamacao", descricao="Chegou  riscado", sessao_id="s1")
    igual = await servico.registrar_solicitacao(CTX, tipo="reclamacao", descricao=" chegou riscado ", sessao_id="s1")
    outra_sessao = await servico.registrar_solicitacao(
        CTX, tipo="reclamacao", descricao="Chegou riscado", sessao_id="s2"
    )
    outro_tipo = await servico.registrar_solicitacao(CTX, tipo="outros", descricao="Chegou riscado", sessao_id="s1")
    outro_tenant = await servico.registrar_solicitacao(
        TenantContext(tenant_id="outra"), tipo="reclamacao", descricao="Chegou riscado", sessao_id="s1"
    )
    assert base.protocolo == igual.protocolo
    assert len({base.protocolo, outra_sessao.protocolo, outro_tipo.protocolo, outro_tenant.protocolo}) == 4


async def test_registro_devolve_protocolo_e_prazo_de_24h_uteis() -> None:
    _, repo = montar()
    r = await ServicoAtendimento(repo, RelogioFixo(local(2026, 3, 10))).registrar_solicitacao(
        CTX, tipo="cotacao_frete", descricao="Frete para Dourados", sessao_id="s1", contato="x@y.com"
    )
    (gravada,) = await repo.listar(CTX)
    assert r.ok and r.protocolo == gravada.id and r.prazo_retorno_horas_uteis == 24
    assert gravada.contato == "x@y.com" and gravada.tipo.value == "cotacao_frete"
    assert r.contato_informado is True


async def test_registro_sem_contato_avisa() -> None:
    _, repo = montar()
    r = await ServicoAtendimento(repo, RelogioFixo(local(2026, 3, 10))).registrar_solicitacao(
        CTX, tipo="outros", descricao="Quero falar com alguém", sessao_id="s1"
    )
    assert r.contato_informado is False and r.aviso


async def test_registro_com_descricao_vazia_ou_tipo_invalido_e_erro() -> None:
    _, repo = montar()
    servico = ServicoAtendimento(repo, RelogioFixo(local(2026, 3, 10)))
    assert not (await servico.registrar_solicitacao(CTX, tipo="outros", descricao="   ", sessao_id="s")).ok
    assert not (await servico.registrar_solicitacao(CTX, tipo="xpto", descricao="oi", sessao_id="s")).ok
    assert await repo.listar(CTX) == ()


# --- fachada ---------------------------------------------------------------------------------------------------------
def test_cliente_recebe_as_seis_tools_com_schema_valido() -> None:
    ferramentas, _ = montar()
    defs = ferramentas.definicoes(Papel.CLIENTE)
    assert {d.nome for d in defs} == TOOLS_CLIENTE
    for d in defs:
        assert len(d.descricao) > 40
        assert d.parametros["type"] == "object" and isinstance(d.parametros["properties"], dict)
        bruto = json.dumps(d.parametros)
        assert "$ref" not in bruto and "$defs" not in bruto and '"title"' not in bruto
        json.loads(bruto)
    por_nome = {d.nome: d for d in defs}
    assert por_nome["consultar_pedido"].parametros["required"] == ["numero", "email_ou_telefone"]
    assert "sessao_id" not in por_nome["registrar_solicitacao"].parametros["properties"]
    itens = por_nome["simular_pagamento"].parametros["properties"]["itens"]
    assert itens["type"] == "array" and itens["items"]["properties"]["quantidade"]["minimum"] == 1


async def test_filtro_por_papel_tools_admin_nao_vao_ao_cliente() -> None:
    ferramentas, _ = montar()

    class SemArgs(BaseModel):
        pass

    class Ok(BaseModel):
        feito: bool = True

    async def admin(ctx: TenantContext, args: BaseModel, sessao_id: str | None) -> BaseModel:
        return Ok()

    ferramentas.registrar(
        Tool(
            nome="tool_admin_teste",
            descricao="Tool administrativa fictícia usada apenas para testar o filtro por papel.",
            argumentos=SemArgs,
            papeis=frozenset({Papel.GERENTE, Papel.ADMIN_PLATAFORMA}),
            funcao=admin,
        )
    )
    assert {d.nome for d in ferramentas.definicoes(Papel.CLIENTE)} == TOOLS_CLIENTE
    assert "tool_admin_teste" in {d.nome for d in ferramentas.definicoes(Papel.GERENTE)}
    negado = await ferramentas.executar(CTX, "tool_admin_teste", {})
    assert not negado.ok and negado.codigo_erro == "tool_nao_permitida"
    liberado = await ferramentas.executar(TenantContext("emporio", papel=Papel.GERENTE), "tool_admin_teste", {})
    assert liberado.ok


async def test_tool_desconhecida_e_argumentos_invalidos_viram_erro_estruturado() -> None:
    ferramentas, _ = montar()
    r = await ferramentas.executar(CTX, "apagar_tudo", {})
    assert not r.ok and r.codigo_erro == "tool_desconhecida" and r.nome == "apagar_tudo"
    for nome, args in [
        ("consultar_pedido", {"numero": "abc", "email_ou_telefone": "a@b.c"}),
        ("consultar_pedido", {"numero": 1}),
        ("simular_pagamento", {"itens": [{"produto_id": 1, "quantidade": 0}]}),
        ("simular_pagamento", {"itens": [{"produto_id": 1, "quantidade": 1}], "forma": "cheque"}),
        ("detalhar_produto", {}),
        ("buscar_produtos", {"preco_max": -5}),
        ("buscar_produtos", {"campo_inexistente": 1}),
    ]:
        r = await ferramentas.executar(CTX, nome, args)
        assert not r.ok and r.codigo_erro == "argumentos_invalidos", nome
        assert r.erro


async def test_erro_de_argumento_nao_ecoa_dado_do_cliente() -> None:
    ferramentas, _ = montar()
    r = await ferramentas.executar(CTX, "consultar_pedido", {"numero": "segredo@x.com", "email_ou_telefone": "a"})
    assert "segredo" not in json.dumps(r.para_dict())


async def test_resultado_e_json_serializavel_e_traz_nome_da_tool() -> None:
    ferramentas, _ = montar()
    r = await ferramentas.executar(CTX, "buscar_produtos", {"categoria": "Violões", "preco_max": 1000})
    assert r.ok and r.nome == "buscar_produtos"
    dados = json.loads(r.para_json())
    assert dados["nome"] == "buscar_produtos" and dados["dados"]["produtos"]
    assert "None" not in r.para_json() and "null" not in r.para_json()  # compacto: campos vazios omitidos


async def test_excecao_inesperada_vira_erro_interno() -> None:
    ferramentas, _ = montar()

    async def quebrada(*_: object, **__: object) -> None:
        raise RuntimeError("boom com dado sensível")

    ferramentas._catalogo.buscar_produtos = quebrada  # type: ignore[method-assign]
    r = await ferramentas.executar(CTX, "buscar_produtos", {"texto": "x"})
    assert not r.ok and r.codigo_erro == "erro_interno" and "sensível" not in json.dumps(r.para_dict())


async def test_executar_cada_tool_ponta_a_ponta() -> None:
    ferramentas, repo = montar()
    r1 = await ferramentas.executar(CTX, "detalhar_produto", {"nome": "Takamine GD20"})
    assert r1.ok and r1.dados["id"] == 10
    r2 = await ferramentas.executar(
        CTX,
        "simular_pagamento",
        {"itens": [{"produto_id": 1, "quantidade": 1}], "forma": "pix", "cidade": "Campo Grande"},
    )
    assert r2.ok and r2.dados["total_pix"]["centavos"] == 47_500
    r3 = await ferramentas.executar(
        CTX, "consultar_pedido", {"numero": 100, "email_ou_telefone": "maria.souza@exemplo.com"}
    )
    assert r3.ok and r3.dados["status"] == "delivered"
    r4 = await ferramentas.executar(CTX, "status_loja", {})
    assert r4.ok and "aberta" in r4.dados
    r5 = await ferramentas.executar(
        CTX, "registrar_solicitacao", {"tipo": "reclamacao", "descricao": "Chegou riscado"}, sessao_id="s9"
    )
    assert r5.ok and (await repo.listar(CTX))[0].sessao_id == "s9"


async def test_resultado_omite_listas_vazias() -> None:
    ferramentas, _ = montar()
    r = await ferramentas.executar(CTX, "detalhar_produto", {"produto_id": 1})
    assert "candidatos" not in r.dados and "equivalentes" not in r.dados and "alternativas" not in r.dados
