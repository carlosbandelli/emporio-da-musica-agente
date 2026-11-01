"""Cenários ponta a ponta das regras de negócio (task 1.13).

Motor nativo + tools + CSVs reais; só o LLM é roteirizado (`LLMFalso`). Cada cenário confere: a tool certa com os
argumentos certos, os valores do resultado contra o CSV, e o texto final coerente com a tool (a guarda de valores não
corrige à toa: o texto sai como o roteiro escreveu, com 2 chamadas ao LLM e `degradada=False`).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from emporio.adaptadores.llm import chamar_tool, responder_texto
from emporio.adaptadores.motor.comum.guarda import valores_monetarios
from emporio.portas import PapelMensagem
from tests.e2e.apoio import (
    argumentos_chamados,
    cliente_do_pedido,
    conversar,
    desconto_ativo,
    montar_com_roteiro,
    pedidos_csv,
    preco_vigente_centavos,
    produto_por_nome,
    produtos_csv,
    redigir,
    resultado_da_tool,
    resultados_das_tools,
)
from tests.modulos.fakes import local

TERCA_MEIO_DIA = local(2026, 3, 10, 12)
"""Terça-feira 12:00 em Campo Grande: loja aberta, depois de todos os pedidos do CSV exceto os de março."""


def _centavos_do_texto(texto: str) -> set[int]:
    return {v.centavos for v in valores_monetarios(texto)}


# --- 1. violões até R$ 1.000 ------------------------------------------------------------------------------------------
async def test_01_violoes_ate_mil_reais_lista_so_ativos_com_preco_vigente_no_teto(tmp_path: Path) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        linhas = [f"{p['nome']}: {p['preco_vigente']['texto']}" for p in res[0]["dados"]["produtos"]]
        return "Violões até R$ 1.000,00:\n" + "\n".join(linhas)

    app, falso = montar_com_roteiro(
        [chamar_tool("buscar_produtos", {"categoria": "Violões", "preco_max": 1000}), redigir(texto)],
        TERCA_MEIO_DIA,
        tmp_path,
    )
    r = await conversar(app, "Quais violões vocês têm até R$ 1000?")

    assert argumentos_chamados(falso) == [("buscar_produtos", {"categoria": "Violões", "preco_max": 1000})]
    dados = resultado_da_tool(falso, "buscar_produtos")
    csv_todos = produtos_csv()
    esperados = {
        pid
        for pid, p in csv_todos.items()
        if p["category_id"] == "5" and p["status"] == "active" and preco_vigente_centavos(p) <= 100_000
    }
    mostrados = {p["id"] for p in dados["produtos"]}
    assert dados["total_encontrado"] == len(esperados) == 13
    assert mostrados <= esperados and len(mostrados) == 10  # a tool limita a lista; o total é informado
    for item in dados["produtos"]:
        origem = csv_todos[item["id"]]
        assert origem["status"] == "active" and item["categoria"] == "Violões"
        assert item["preco_vigente"]["centavos"] == preco_vigente_centavos(origem) <= 100_000
        assert item["preco_tabela"]["centavos"] == int(float(origem["price_brl"]) * 100 + 0.5)
    # descontinuado (113) e inativos não aparecem; nem produtos de outras categorias
    assert 113 not in mostrados and all(csv_todos[i]["category_id"] == "5" for i in mostrados)

    assert r.degradada is False and r.tools_chamadas == ("buscar_produtos",) and len(falso.requisicoes) == 2
    valores_texto = _centavos_do_texto(r.texto)
    assert {p["preco_vigente"]["centavos"] for p in dados["produtos"]} <= valores_texto
    assert max(valores_texto - {100_000}) <= 100_000


# --- 2. endereço da loja (manual, sem tool) -------------------------------------------------------------------------
async def test_02_endereco_vem_do_manual_sem_chamar_tool_de_dados(tmp_path: Path) -> None:
    endereco = "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333"
    app, falso = montar_com_roteiro(
        [responder_texto(f"A gente fica na {endereco}. Passa aqui!")], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, "Qual o endereço da loja?")

    assert endereco in falso.ultima_requisicao.system_cacheavel  # o manual está no prompt do tenant
    assert r.tools_chamadas == () and not any(m.role is PapelMensagem.TOOL for m in falso.ultima_requisicao.mensagens)
    assert r.degradada is False and endereco in r.texto and valores_monetarios(r.texto) == ()
    assert len(falso.requisicoes) == 1


# --- 3. preço do Takamine GD20 ---------------------------------------------------------------------------------------
async def test_03_preco_do_takamine_gd20_vem_da_tool_com_pix_e_parcelas(tmp_path: Path) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        return (
            f"O {d['nome']} custa {d['preco_vigente']['texto']} (PIX: {d['preco_pix']['texto']}, "
            f"ou {d['parcelas_max']}x de {d['parcelamento_max']['valor_primeira_parcela']['texto']} sem juros)."
        )

    app, falso = montar_com_roteiro(
        [chamar_tool("detalhar_produto", {"nome": "Takamine GD20"}), redigir(texto)], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, 'Quanto custa o "Takamine GD20"?')

    assert argumentos_chamados(falso) == [("detalhar_produto", {"nome": "Takamine GD20"})]
    dados = resultado_da_tool(falso, "detalhar_produto")
    origem = produto_por_nome("Takamine GD20")
    assert dados["id"] == int(origem["product_id"]) == 95 and desconto_ativo(95) == 0  # promoção inativa no CSV
    assert dados["preco_tabela"]["centavos"] == dados["preco_vigente"]["centavos"] == 219_900
    assert dados["preco_pix"]["centavos"] == 208_905 and dados["pix_tem_desconto"] is True  # 5% sobre preço cheio
    assert dados["parcelas_max"] == 12 and dados["parcelamento_max"]["valor_primeira_parcela"]["centavos"] == 18_325
    assert dados.get("desconto_percentual") is None and dados["promocao_encerrada"] is True

    assert r.degradada is False and r.tools_chamadas == ("detalhar_produto",) and len(falso.requisicoes) == 2
    assert _centavos_do_texto(r.texto) == {219_900, 208_905, 18_325}
    assert "R$ 2.199,00" in r.texto and "R$ 2.089,05" in r.texto and "12x de R$ 183,25" in r.texto


async def test_03b_com_promocao_ativa_mostra_promo_original_e_percentual_e_pix_sem_desconto_extra(
    tmp_path: Path,
) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        return (
            f"{d['nome']} está com {d['desconto_percentual']}% off: de {d['preco_tabela']['texto']} "
            f"por {d['preco_vigente']['texto']}. No PIX continua {d['preco_pix']['texto']} (promoções não acumulam)."
        )

    app, falso = montar_com_roteiro(
        [chamar_tool("detalhar_produto", {"nome": "Crafter HT-100"}), redigir(texto)], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, "E o Crafter HT-100, quanto está?")

    dados = resultado_da_tool(falso, "detalhar_produto")
    origem = produto_por_nome("Crafter HT-100")
    esperado = preco_vigente_centavos(origem)
    assert desconto_ativo(90) == 18 and dados["desconto_percentual"] == 18
    assert dados["preco_tabela"]["centavos"] == 239_900 and dados["preco_vigente"]["centavos"] == esperado == 196_718
    assert dados["pix_tem_desconto"] is False and dados["preco_pix"]["centavos"] == esperado
    assert r.degradada is False and len(falso.requisicoes) == 2
    assert _centavos_do_texto(r.texto) == {239_900, 196_718}
    assert "18%" in r.texto


# --- 4. arrependimento: janela de 7 dias com a data de referência ---------------------------------------------------
@pytest.mark.parametrize(
    ("hoje", "data_local", "situacao_esperada", "veredito"),
    [
        (local(2026, 2, 20, 10), "2026-02-20", "dentro", "ainda está dentro do prazo de 7 dias"),
        (local(2026, 3, 10, 10), "2026-03-10", "fora", "o prazo de 7 dias já passou"),
    ],
    ids=["dentro-da-janela", "fora-da-janela"],
)
async def test_04_arrependimento_aplica_janela_de_7_dias_com_a_data_de_referencia(
    tmp_path: Path, hoje: datetime, data_local: str, situacao_esperada: str, veredito: str
) -> None:
    pedido = pedidos_csv()[7]  # delivered, recebido (previsão) em 2026-02-17
    cliente = cliente_do_pedido(7)
    assert pedido["status"] == "delivered" and pedido["estimated_delivery"] == "2026-02-17"

    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        arrep = next(j for j in d["devolucao"]["janelas"] if j["tipo"] == "arrependimento")
        return f"Letícia, o pedido {d['numero']} foi entregue e {veredito} (até {arrep['prazo_final']})."

    app, falso = montar_com_roteiro(
        [chamar_tool("consultar_pedido", {"numero": 7, "email_ou_telefone": cliente["email"]}), redigir(texto)],
        hoje,
        tmp_path,
    )
    r = await conversar(app, f"Me arrependi da compra, posso devolver? Pedido 7, meu e-mail é {cliente['email']}")

    assert argumentos_chamados(falso) == [("consultar_pedido", {"numero": 7, "email_ou_telefone": cliente["email"]})]
    dados = resultado_da_tool(falso, "consultar_pedido")
    assert dados["encontrado"] is True and dados["status"] == "delivered" and dados["recebimento_estimado"] is True
    assert dados["devolucao"]["hoje"] == data_local
    arrep = next(j for j in dados["devolucao"]["janelas"] if j["tipo"] == "arrependimento")
    assert arrep["prazo_final"] == "2026-02-24"  # recebimento + 7 dias
    assert arrep["situacao"] == situacao_esperada
    if situacao_esperada == "dentro":
        assert arrep["dias_restantes"] == 4
    assert r.degradada is False and r.tools_chamadas == ("consultar_pedido",) and len(falso.requisicoes) == 2
    assert veredito in r.texto and "2026-02-24" in r.texto


# --- 5. acessório fora de escopo --------------------------------------------------------------------------------------
async def test_05_acessorio_e_detectado_como_fora_de_escopo_e_redireciona(tmp_path: Path) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        assert res[0]["dados"]["fora_de_escopo"] is True
        return (
            "Poxa, a gente vende só instrumentos musicais, não temos cordas nem outros acessórios. "
            "Se quiser, te mostro violões e guitarras!"
        )

    app, falso = montar_com_roteiro(
        [chamar_tool("buscar_produtos", {"texto": "cordas"}), redigir(texto)], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, "Vocês têm cordas?")

    dados = resultado_da_tool(falso, "buscar_produtos")
    assert dados["fora_de_escopo"] is True and "cordas" in dados["motivo"]
    assert "produtos" not in dados and dados["total_encontrado"] == 0  # nenhum item de catálogo devolvido
    assert r.degradada is False and r.tools_chamadas == ("buscar_produtos",)
    assert "só instrumentos" in r.texto and valores_monetarios(r.texto) == ()


async def test_05b_violao_com_cordas_de_aco_nao_e_fora_de_escopo(tmp_path: Path) -> None:
    app, falso = montar_com_roteiro(
        [chamar_tool("buscar_produtos", {"texto": "violão cordas de aço"}), redigir(lambda _: "Achei opções!")],
        TERCA_MEIO_DIA,
        tmp_path,
    )
    r = await conversar(app, "Quero um violão com cordas de aço")

    dados = resultado_da_tool(falso, "buscar_produtos")
    assert dados["fora_de_escopo"] is False and dados["produtos"]  # o acessório é só característica do instrumento
    assert r.degradada is False


# --- 6. produto sem estoque ------------------------------------------------------------------------------------------
async def test_06_produto_sem_estoque_vem_com_alternativas_em_estoque(tmp_path: Path) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        alts = ", ".join(f"{a['nome']} ({a['preco_vigente']['texto']})" for a in d["alternativas"])
        return f"O {d['nome']} está sem estoque agora. Que tal: {alts}?"

    origem = produto_por_nome("Giannini GF-3D")
    assert origem["stock_quantity"] == "0" and origem["status"] == "active"
    app, falso = montar_com_roteiro(
        [chamar_tool("detalhar_produto", {"nome": "Giannini GF-3D"}), redigir(texto)], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, "Tem o violão Giannini GF-3D?")

    dados = resultado_da_tool(falso, "detalhar_produto")
    assert dados["id"] == 96 and dados["em_estoque"] is False and dados["estoque"] == 0
    assert dados["disponibilidade"] == "sem_estoque" and dados["alternativas"]
    todos = produtos_csv()
    for alt in dados["alternativas"]:
        fonte = todos[alt["id"]]
        assert fonte["status"] == "active" and int(fonte["stock_quantity"]) > 0 and alt["em_estoque"] is True
        assert fonte["category_id"] == origem["category_id"] and alt["id"] != 96
        assert alt["preco_vigente"]["centavos"] == preco_vigente_centavos(fonte)
    assert r.degradada is False and len(falso.requisicoes) == 2
    assert {a["preco_vigente"]["centavos"] for a in dados["alternativas"]} == _centavos_do_texto(r.texto)
    assert "sem estoque" in r.texto


# --- 7. produto descontinuado ----------------------------------------------------------------------------------------
async def test_07_produto_descontinuado_oferece_equivalentes(tmp_path: Path) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        eq = ", ".join(f"{a['nome']} ({a['preco_vigente']['texto']})" for a in d["equivalentes"])
        return f"O {d['nome']} foi descontinuado e não é mais vendido. Equivalentes: {eq}."

    origem = produto_por_nome("Shelby SN-7C")
    assert origem["status"] == "discontinued"
    app, falso = montar_com_roteiro(
        [chamar_tool("detalhar_produto", {"nome": "Shelby SN-7C"}), redigir(texto)], TERCA_MEIO_DIA, tmp_path
    )
    r = await conversar(app, "Quero o violão 7 cordas Shelby SN-7C")

    dados = resultado_da_tool(falso, "detalhar_produto")
    assert dados["id"] == 113 and dados["status"] == "discontinued" and dados["disponibilidade"] == "descontinuado"
    assert dados["em_estoque"] is False and dados["equivalentes"]
    todos = produtos_csv()
    for eq in dados["equivalentes"]:
        fonte = todos[eq["id"]]
        assert fonte["status"] == "active" and int(fonte["stock_quantity"]) > 0
        assert fonte["category_id"] == origem["category_id"] and eq["substitui_id"] == 113
    assert r.degradada is False and "descontinuado" in r.texto and len(falso.requisicoes) == 2
    assert {e["preco_vigente"]["centavos"] for e in dados["equivalentes"]} == _centavos_do_texto(r.texto)


# --- 8. verificação de identidade falha ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("numero", "prova"),
    [
        (7, "outra.pessoa@jmail.com"),  # e-mail errado
        (7, "pedro.oliveira@jmail.com"),  # e-mail de OUTRO cliente do CSV
        (7, "(67) 90000-0000"),  # telefone errado
    ],
    ids=["email-errado", "email-de-outro-cliente", "telefone-errado"],
)
async def test_08_verificacao_falha_nao_vaza_nada_do_pedido(tmp_path: Path, numero: int, prova: str) -> None:
    cliente = cliente_do_pedido(numero)
    segredos = [
        cliente["name"].split()[0],
        cliente["name"],
        cliente["email"],
        cliente["phone"],
        "delivered",
        "Shelby SU-21S",
        "BRKR1122334BR",
        "159,90",
        "2026-02-17",
    ]

    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        return str(res[0]["dados"]["mensagem"])

    app, falso = montar_com_roteiro(
        [chamar_tool("consultar_pedido", {"numero": numero, "email_ou_telefone": prova}), redigir(texto)],
        TERCA_MEIO_DIA,
        tmp_path,
    )
    r = await conversar(app, f"Cadê o meu pedido {numero}? Meu contato: {prova}")

    bruto = next(m.content for m in falso.ultima_requisicao.mensagens if m.role is PapelMensagem.TOOL)
    dados = resultado_da_tool(falso, "consultar_pedido")
    assert dados["encontrado"] is False and set(dados) <= {"encontrado", "mensagem", "recebimento_estimado"}
    for segredo in segredos:
        assert segredo not in bruto and segredo not in r.texto, segredo
    assert r.degradada is False and "Não encontrei um pedido" in r.texto
    # a resposta é a mesma para pedido inexistente: não revela se o pedido existe
    inexistente = montar_com_roteiro(
        [chamar_tool("consultar_pedido", {"numero": 9999, "email_ou_telefone": prova}), redigir(texto)],
        TERCA_MEIO_DIA,
        tmp_path / "inexistente",
    )
    r2 = await conversar(inexistente[0], "pedido 9999")
    assert r2.texto == r.texto


# --- 9. fora do expediente -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("agora", "motivo", "dia_reabertura"),
    [
        (local(2026, 3, 8, 10), "fim_de_semana", "segunda-feira"),  # domingo
        (local(2026, 3, 7, 14), "fora_do_horario", "segunda-feira"),  # sábado depois das 13h
    ],
    ids=["domingo-10h", "sabado-14h"],
)
async def test_09_fora_do_expediente_informa_fechado_e_quando_reabre(
    tmp_path: Path, agora: datetime, motivo: str, dia_reabertura: str
) -> None:
    def texto(res: list[dict]) -> str:  # type: ignore[type-arg]
        d = res[0]["dados"]
        assert d["aberta"] is False
        return f"Estamos fechados agora, mas reabrimos {d['proxima_abertura_dia']} às 09:00."

    app, falso = montar_com_roteiro([chamar_tool("status_loja", {}), redigir(texto)], agora, tmp_path)
    r = await conversar(app, "A loja está aberta agora?")

    assert argumentos_chamados(falso) == [("status_loja", {})]
    dados = resultado_da_tool(falso, "status_loja")
    assert dados["aberta"] is False and dados["motivo"] == motivo
    assert dados["proxima_abertura"] == "2026-03-09T09:00-04:00" and dados["proxima_abertura_dia"] == dia_reabertura
    assert any("Sábado: 09:00 às 13:00" in h for h in dados["horarios"])
    assert r.degradada is False and r.tools_chamadas == ("status_loja",) and len(falso.requisicoes) == 2
    assert "fechados" in r.texto and f"reabrimos {dia_reabertura} às 09:00" in r.texto


async def test_09b_em_horario_comercial_a_loja_esta_aberta(tmp_path: Path) -> None:
    app, falso = montar_com_roteiro(
        [chamar_tool("status_loja", {}), redigir(lambda r: f"Aberta até {r[0]['dados']['fechamento_hoje']}.")],
        TERCA_MEIO_DIA,
        tmp_path,
    )
    r = await conversar(app, "A loja está aberta?")
    dados = resultado_da_tool(falso, "status_loja")
    assert dados["aberta"] is True and dados["fechamento_hoje"] == "18:00" and "18:00" in r.texto


# --- 10. frete fora de Campo Grande -> cotação registrada -----------------------------------------------------------
async def test_10_frete_fora_de_campo_grande_registra_cotacao_com_protocolo(tmp_path: Path) -> None:
    contato = "cliente.corumba@jmail.com"
    descricao = "Cotação de frete do Takamine GD20 (id 95) para Corumbá/MS"

    def texto(res: list[dict]) -> str:
        frete = res[0]["dados"]["frete"]
        assert frete["requer_cotacao"] is True and frete["em_campo_grande"] is False
        return "Para Corumbá o frete depende de cotação. Vou pedir para a equipe!"

    def fechamento(req_resultados: list[dict]) -> str:  # type: ignore[type-arg]
        solicitacao = req_resultados[-1]["dados"]
        return f"Pronto! Registrei sua cotação, protocolo {solicitacao['protocolo']}. Retorno em até 24 horas úteis."

    app, falso = montar_com_roteiro(
        [
            chamar_tool("simular_pagamento", {"itens": [{"produto_id": 95, "quantidade": 1}], "cidade": "Corumbá"}),
            chamar_tool("registrar_solicitacao", {"tipo": "cotacao_frete", "descricao": descricao, "contato": contato}),
            redigir(fechamento),
        ],
        TERCA_MEIO_DIA,
        tmp_path,
    )
    r = await conversar(app, "Quanto fica o frete do Takamine GD20 para Corumbá? Meu e-mail: " + contato)

    assert [n for n, _ in argumentos_chamados(falso)] == ["simular_pagamento", "registrar_solicitacao"]
    frete = resultados_das_tools(falso)[0]["dados"]["frete"]
    assert (
        frete["requer_cotacao"] is True and frete["motivo_cotacao"] == "fora_de_campo_grande" and "valor" not in frete
    )
    assert {o["modalidade"] for o in frete["opcoes"]} == {"pac", "sedex", "jadlog"}
    registro = resultados_das_tools(falso)[1]["dados"]
    assert (
        registro["ok"] is True and registro["tipo"] == "cotacao_frete" and registro["prazo_retorno_horas_uteis"] == 24
    )
    assert registro["protocolo"].startswith("EMP-") and registro["contato_informado"] is True

    arquivo = tmp_path / "emporio" / "solicitacoes.jsonl"
    linhas = [json.loads(linha) for linha in arquivo.read_text(encoding="utf-8").splitlines() if linha.strip()]
    assert len(linhas) == 1
    assert linhas[0]["id"] == registro["protocolo"] and linhas[0]["tipo"] == "cotacao_frete"
    assert linhas[0]["descricao"] == descricao and linhas[0]["contato"] == contato and linhas[0]["sessao_id"] == "e2e"

    assert r.degradada is False and r.tools_chamadas == ("simular_pagamento", "registrar_solicitacao")
    assert registro["protocolo"] in r.texto and len(falso.requisicoes) == 3
