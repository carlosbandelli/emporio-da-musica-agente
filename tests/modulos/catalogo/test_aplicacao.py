"""Casos de uso do catálogo: busca e detalhe de produtos."""

from decimal import Decimal

import pytest

from emporio.modulos.catalogo.aplicacao import ServicoCatalogo
from tests.modulos.fakes import CTX, CTX_EN, CatalogoFalso, produto, resolver_config


def servico(catalogo: CatalogoFalso | None = None) -> ServicoCatalogo:
    return ServicoCatalogo(catalogo or CatalogoFalso(), resolver_config)


async def test_violoes_ate_mil_reais_compara_preco_vigente_e_so_ativos() -> None:
    r = await servico().buscar_produtos(CTX, categoria="Violões", preco_max_reais=Decimal(1000))
    ids = {p.id for p in r.produtos}
    assert ids == {1, 2, 3, 8, 9}  # 8 custa R$ 1.100 na tabela, R$ 880 vigente
    assert all(p.preco_vigente.centavos <= 100_000 for p in r.produtos)
    assert not ({5, 6, 12} & ids)  # descontinuado e em breve ficam de fora
    promo = next(p for p in r.produtos if p.id == 8)
    assert promo.desconto_percentual == 20
    assert promo.preco_tabela.centavos == 110_000 and promo.preco_vigente.centavos == 88_000


async def test_busca_ordena_com_estoque_primeiro_e_preco_crescente() -> None:
    r = await servico().buscar_produtos(CTX, categoria=5)
    em_estoque = [p.em_estoque for p in r.produtos]
    assert em_estoque == sorted(em_estoque, reverse=True)
    precos = [p.preco_vigente.centavos for p in r.produtos if p.em_estoque]
    assert precos == sorted(precos)


async def test_busca_limita_resultados() -> None:
    muitos = tuple(produto(200 + n, f"Violão Muitos {n}", 100 + n) for n in range(15))
    r = await servico(CatalogoFalso(muitos)).buscar_produtos(CTX, categoria=5)
    assert len(r.produtos) == 10 and r.total_encontrado == 15


async def test_sem_estoque_traz_alternativas_da_mesma_categoria_com_preco_proximo() -> None:
    r = await servico().buscar_produtos(CTX, texto="Beta")
    assert [p.id for p in r.produtos] == [2]
    assert r.produtos[0].em_estoque is False
    assert r.alternativas
    ids = [a.id for a in r.alternativas]
    assert ids[:2] == [8, 3]  # preço vigente mais próximo de R$ 900: R$ 880, depois R$ 950
    assert 2 not in ids and all(a.em_estoque for a in r.alternativas)
    assert all(a.substitui_id == 2 for a in r.alternativas)


async def test_busca_sem_item_fora_de_estoque_nao_traz_alternativas() -> None:
    r = await servico().buscar_produtos(CTX, texto="Alfa")
    assert [p.id for p in r.produtos] == [1] and r.alternativas == ()


@pytest.mark.parametrize(
    "consulta", ["cordas para violão", "Guitar strings", "palhetas", "uma corda de nylon", "CABOS"]
)
async def test_acessorios_sao_fora_de_escopo(consulta: str) -> None:
    r = await servico().buscar_produtos(CTX, texto=consulta)
    assert r.fora_de_escopo is True
    assert r.produtos == () and r.motivo


@pytest.mark.parametrize("categoria", [6, "7", "Cordas Orquestrais", "sopro"])
async def test_categorias_sem_produtos(categoria: int | str) -> None:
    r = await servico().buscar_produtos(CTX, categoria=categoria)
    assert r.sem_itens_na_categoria is True and r.produtos == ()
    assert r.observacao and "nunca" not in r.observacao.lower()


async def test_categoria_inexistente_lista_validas() -> None:
    r = await servico().buscar_produtos(CTX, categoria="Gaitas de fole")
    assert r.categoria_nao_encontrada is True and "Violões" in r.categorias_validas


@pytest.mark.parametrize("categoria", ["violão", "violoes", "Violões", "5", 5])
async def test_categoria_por_nome_ou_id(categoria: int | str) -> None:
    r = await servico().buscar_produtos(CTX, categoria=categoria)
    assert r.produtos and all(p.categoria == "Violões" for p in r.produtos)


async def test_texto_no_plural_acha_singular() -> None:
    r = await servico().buscar_produtos(CTX, texto="violões Alfa")
    assert [p.id for p in r.produtos] == [1]


async def test_preco_minimo_usa_preco_vigente() -> None:
    r = await servico().buscar_produtos(CTX, categoria=5, preco_min_reais=Decimal(900))
    assert 8 not in {p.id for p in r.produtos}  # vigente R$ 880
    assert {p.id for p in r.produtos} == {2, 3, 4, 10, 11}


async def test_busca_formata_valores_no_idioma() -> None:
    r = await servico().buscar_produtos(CTX_EN, texto="Alfa")
    assert r.produtos[0].preco_vigente.texto == "R$500.00"


# --- detalhar_produto ----------------------------------------------------------------------------------------------
async def test_takamine_gd20_por_nome() -> None:
    d = await servico().detalhar_produto(CTX, nome="Takamine GD20")
    assert d.encontrado and d.id == 10 and d.nome.startswith("Takamine GD20")
    assert d.preco_vigente is not None and d.preco_vigente.centavos == 219_900
    assert d.preco_pix is not None and d.preco_pix.centavos == 208_905
    assert d.parcelas_max == 12
    assert d.parcelamento_max is not None and d.parcelamento_max.parcelas == 12
    assert d.disponibilidade == "disponivel" and d.em_estoque


async def test_nome_ambiguo_devolve_candidatos() -> None:
    d = await servico().detalhar_produto(CTX, nome="Takamine")
    assert d.ambiguo and not d.id
    assert {c.id for c in d.candidatos} == {10, 11}


async def test_nome_com_erro_de_digitacao_acha_por_aproximacao() -> None:
    d = await servico().detalhar_produto(CTX, nome="Takamne GD20")
    assert d.encontrado and d.id == 10


async def test_nome_inexistente() -> None:
    d = await servico().detalhar_produto(CTX, nome="Saxofone Bruxo 3000")
    assert not d.encontrado and not d.ambiguo
    assert not d.fora_de_escopo


async def test_detalhar_acessorio_inexistente_sinaliza_fora_de_escopo() -> None:
    d = await servico().detalhar_produto(CTX, nome="cordas Elixir")
    assert not d.encontrado and d.fora_de_escopo


async def test_detalhar_por_id_e_sem_parametros() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=1)
    assert d.encontrado and d.nome == "Violão Alfa" and d.estoque == 3
    assert d.specs == {"cor": "natural"}
    nada = await servico().detalhar_produto(CTX)
    assert not nada.encontrado and nada.observacao
    assert not (await servico().detalhar_produto(CTX, produto_id=424242)).encontrado


async def test_estoque_vem_do_repositorio_a_cada_chamada() -> None:
    cat = CatalogoFalso()
    s = servico(cat)
    assert (await s.detalhar_produto(CTX, produto_id=1)).estoque == 3
    cat.produtos[1] = produto(1, "Violão Alfa", 500, estoque=0)
    antes = await s.detalhar_produto(CTX, produto_id=1)
    assert antes.estoque == 0 and not antes.em_estoque and antes.disponibilidade == "sem_estoque"


async def test_sem_estoque_oferece_alternativas() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=2)
    assert d.disponibilidade == "sem_estoque" and not d.em_estoque
    assert d.alternativas and d.alternativas[0].id == 8
    assert d.equivalentes == ()


async def test_descontinuado_oferece_equivalentes() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=5)
    assert d.disponibilidade == "descontinuado" and d.status == "discontinued"
    assert d.equivalentes and all(e.em_estoque for e in d.equivalentes)
    assert 5 not in {e.id for e in d.equivalentes}
    assert d.preco_pix is None and d.parcelas_max is None  # não se vende mais


async def test_em_breve_avisa_que_nao_esta_disponivel() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=6)
    assert d.disponibilidade == "em_breve" and d.observacao
    assert d.preco_pix is None


async def test_promocao_ativa_mostra_tabela_vigente_e_percentual() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=8)
    assert d.desconto_percentual == 20 and d.descricao_promocao == "Black Friday"
    assert d.preco_tabela is not None and d.preco_tabela.centavos == 110_000
    assert d.preco_vigente is not None and d.preco_vigente.centavos == 88_000
    assert d.preco_pix is not None and d.preco_pix.centavos == 88_000 and d.pix_tem_desconto is False
    assert d.promocao_encerrada is False


async def test_promocao_expirada_avisa_e_traz_preco_atual() -> None:
    d = await servico().detalhar_produto(CTX, produto_id=9)
    assert d.promocao_encerrada is True and d.desconto_percentual is None
    assert d.preco_vigente is not None and d.preco_vigente.centavos == 70_000
    assert d.preco_pix is not None and d.preco_pix.centavos == 66_500
