"""Casos de uso de políticas: horário da loja, frete e janelas de devolução."""

from datetime import date, datetime

import pytest

from emporio.modulos.politicas.aplicacao import ServicoPoliticas
from tests.modulos.fakes import CTX, CTX_EN, RelogioFixo, local, resolver_config


def servico(instante: datetime) -> ServicoPoliticas:
    return ServicoPoliticas(RelogioFixo(instante), resolver_config)


def test_loja_aberta_em_dia_util() -> None:
    r = servico(local(2026, 3, 9, 10)).status_loja(CTX)  # segunda 10h
    assert r.aberta is True
    assert r.motivo is None
    assert r.fechamento_hoje == "18:00"


def test_loja_fechada_apos_18h_informa_proxima_abertura() -> None:
    r = servico(local(2026, 3, 9, 19)).status_loja(CTX)
    assert r.aberta is False
    assert r.motivo == "fora_do_horario"
    assert r.proxima_abertura.startswith("2026-03-10T09:00")
    assert r.proxima_abertura_dia == "terça-feira"


def test_sabado_apos_13h_reabre_na_segunda() -> None:
    r = servico(local(2026, 3, 14, 14)).status_loja(CTX)
    assert not r.aberta
    assert r.proxima_abertura.startswith("2026-03-16T09:00")
    assert r.proxima_abertura_dia == "segunda-feira"


def test_domingo_fechado() -> None:
    r = servico(local(2026, 3, 15, 10)).status_loja(CTX)
    assert not r.aberta
    assert r.motivo == "fim_de_semana"


def test_feriado_nacional_fechado_com_nome() -> None:
    r = servico(local(2026, 9, 7, 10)).status_loja(CTX)  # Independência, segunda
    assert not r.aberta
    assert r.motivo == "feriado"
    assert r.feriado
    assert r.proxima_abertura.startswith("2026-09-08T09:00")


def test_status_traz_horarios_e_contatos_do_tenant() -> None:
    r = servico(local(2026, 3, 9)).status_loja(CTX)
    assert len(r.horarios) == 3
    assert r.contatos.whatsapp == "(67) 3321-4500"
    assert r.contatos.telefone == "(67) 3341-4444"
    assert r.contatos.email == "contato@emporiodamusica.com.br"


def test_dia_da_semana_em_ingles() -> None:
    r = servico(local(2026, 3, 9, 19)).status_loja(CTX_EN)
    assert r.proxima_abertura_dia == "Tuesday"


@pytest.mark.parametrize(
    ("cidade", "total", "gratis", "valor"),
    [
        ("Campo Grande", 49_999, False, 3_500),
        ("Campo Grande/MS", 50_000, True, 0),
        ("campo grande", 120_000, True, 0),
    ],
)
def test_frete_em_campo_grande(cidade: str, total: int, gratis: bool, valor: int) -> None:
    f = servico(local(2026, 3, 9)).cotar_frete(CTX, cidade, total)
    assert f.em_campo_grande and f.gratis is gratis
    assert f.valor is not None and f.valor.centavos == valor
    assert not f.requer_cotacao
    assert f.opcoes[0].modalidade == "entrega_local"


def test_frete_fora_de_campo_grande_nao_informa_valor() -> None:
    f = servico(local(2026, 3, 9)).cotar_frete(CTX, "Dourados", 300_000)
    assert f.valor is None and f.requer_cotacao
    assert {o.modalidade for o in f.opcoes} == {"pac", "sedex", "jadlog"}
    assert f.motivo_cotacao == "fora_de_campo_grande"


def test_frete_formata_valor_no_idioma() -> None:
    f = servico(local(2026, 3, 9)).cotar_frete(CTX_EN, "Campo Grande", 10_000)
    assert f.valor is not None and f.valor.texto == "R$35.00"


def test_frete_cidade_vazia_vira_erro_estruturado() -> None:
    f = servico(local(2026, 3, 9)).cotar_frete(CTX, "  /MS", 10_000)
    assert f.erro


def test_devolucao_arrependimento_dentro_e_fora_do_prazo() -> None:
    args = {"data_compra": date(2026, 3, 1), "status": "delivered", "data_recebimento": date(2026, 3, 5)}
    dentro = servico(local(2026, 3, 12)).avaliar_devolucao(**args)  # type: ignore[arg-type]  # 7º dia
    fora = servico(local(2026, 3, 13)).avaliar_devolucao(**args)  # type: ignore[arg-type]  # 8º dia
    j_dentro = {j.tipo: j for j in dentro.janelas}
    j_fora = {j.tipo: j for j in fora.janelas}
    assert j_dentro["arrependimento"].situacao == "dentro"
    assert j_fora["arrependimento"].situacao == "fora"
    assert j_fora["garantia_legal"].situacao == "dentro"
    assert dentro.hoje == date(2026, 3, 12)


def test_devolucao_com_hoje_anterior_a_compra_vira_erro() -> None:
    r = servico(local(2026, 2, 1)).avaliar_devolucao(
        data_compra=date(2026, 3, 1), status="pending", data_recebimento=None
    )
    assert r.erro and not r.janelas
