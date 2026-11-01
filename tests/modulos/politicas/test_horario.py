"""Testes de HorarioLoja: expediente, feriados nacionais e próxima abertura."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emporio.modulos.politicas.dominio import (
    FUSO_LOJA,
    ErroDatetimeSemFuso,
    ErroPoliticas,
    HorarioLoja,
    MotivoFechamento,
)

CG = ZoneInfo("America/Campo_Grande")
LOJA = HorarioLoja()


def local(ano: int, mes: int, dia: int, hora: int = 0, minuto: int = 0) -> datetime:
    return datetime(ano, mes, dia, hora, minuto, tzinfo=CG)


def test_fuso_da_loja_e_campo_grande() -> None:
    assert FUSO_LOJA.key == "America/Campo_Grande"


def test_sexta_1759_aberta_e_1800_fechada_com_proxima_abertura_no_sabado() -> None:
    sexta_1759 = local(2026, 1, 9, 17, 59)
    sexta_1800 = local(2026, 1, 9, 18, 0)

    assert LOJA.esta_aberta(sexta_1759) is True
    assert LOJA.esta_aberta(sexta_1800) is False
    assert LOJA.proxima_abertura(sexta_1800) == local(2026, 1, 10, 9, 0)


def test_abre_as_0900_e_nao_as_0859() -> None:
    assert LOJA.esta_aberta(local(2026, 1, 12, 8, 59)) is False
    assert LOJA.esta_aberta(local(2026, 1, 12, 9, 0)) is True


def test_sabado_aberto_ate_1259_e_fecha_as_1300_com_proxima_abertura_na_segunda() -> None:
    assert LOJA.esta_aberta(local(2026, 1, 10, 12, 59)) is True
    assert LOJA.esta_aberta(local(2026, 1, 10, 13, 0)) is False
    assert LOJA.proxima_abertura(local(2026, 1, 10, 13, 0)) == local(2026, 1, 12, 9, 0)


def test_domingo_fechado_e_proxima_abertura_na_segunda() -> None:
    domingo = local(2026, 1, 11, 10, 0)
    assert LOJA.esta_aberta(domingo) is False
    assert LOJA.proxima_abertura(domingo) == local(2026, 1, 12, 9, 0)


def test_dia_util_antes_da_abertura_reabre_no_mesmo_dia() -> None:
    assert LOJA.proxima_abertura(local(2026, 1, 13, 7, 0)) == local(2026, 1, 13, 9, 0)


def test_feriado_nacional_fecha_e_proxima_abertura_e_o_dia_seguinte() -> None:
    tiradentes = local(2026, 4, 21, 10, 0)  # terça-feira
    assert LOJA.esta_aberta(tiradentes) is False
    assert LOJA.proxima_abertura(tiradentes) == local(2026, 4, 22, 9, 0)


def test_feriado_na_segunda_empurra_a_abertura_para_a_terca() -> None:
    # Finados, 2026-11-02, é segunda-feira.
    assert LOJA.proxima_abertura(local(2026, 10, 31, 14, 0)) == local(2026, 11, 3, 9, 0)
    assert LOJA.proxima_abertura(local(2026, 11, 2, 10, 0)) == local(2026, 11, 3, 9, 0)


def test_aberta_devolve_o_proprio_instante_como_proxima_abertura() -> None:
    agora = local(2026, 1, 13, 10, 30)
    assert LOJA.proxima_abertura(agora) == agora


def test_status_aberta() -> None:
    status = LOJA.status(local(2026, 1, 9, 17, 59))
    assert status.aberta is True
    assert status.motivo is None
    assert status.fechamento_hoje == local(2026, 1, 9, 18, 0)
    assert status.nome_feriado is None


def test_status_sabado_fecha_as_13() -> None:
    assert LOJA.status(local(2026, 1, 10, 9, 0)).fechamento_hoje == local(2026, 1, 10, 13, 0)


def test_status_fora_do_horario() -> None:
    status = LOJA.status(local(2026, 1, 9, 19, 0))
    assert status.aberta is False
    assert status.motivo is MotivoFechamento.FORA_DO_HORARIO
    assert status.fechamento_hoje == local(2026, 1, 9, 18, 0)
    assert status.proxima_abertura == local(2026, 1, 10, 9, 0)


def test_status_sabado_apos_o_expediente_e_fora_do_horario() -> None:
    status = LOJA.status(local(2026, 1, 10, 14, 0))
    assert status.motivo is MotivoFechamento.FORA_DO_HORARIO


def test_status_domingo_e_fim_de_semana_sem_fechamento_hoje() -> None:
    status = LOJA.status(local(2026, 1, 11, 10, 0))
    assert status.aberta is False
    assert status.motivo is MotivoFechamento.FIM_DE_SEMANA
    assert status.fechamento_hoje is None


def test_status_feriado() -> None:
    status = LOJA.status(local(2026, 4, 21, 10, 0))
    assert status.aberta is False
    assert status.motivo is MotivoFechamento.FERIADO
    assert status.fechamento_hoje is None
    assert status.nome_feriado == "Tiradentes"
    assert status.proxima_abertura == local(2026, 4, 22, 9, 0)


def test_feriado_em_domingo_conta_como_feriado() -> None:
    # 2026-11-15 (Proclamação da República) cai num domingo.
    status = LOJA.status(local(2026, 11, 15, 10, 0))
    assert status.motivo is MotivoFechamento.FERIADO


def test_virada_de_ano_proxima_abertura_atravessa_o_ano() -> None:
    # 2026-12-25 (sexta, Natal) -> sábado 26 abre.
    assert LOJA.proxima_abertura(local(2026, 12, 25, 10, 0)) == local(2026, 12, 26, 9, 0)
    # 2026-12-31 (quinta) 19h -> 2027-01-01 é feriado (sexta) -> sábado 02/01.
    assert LOJA.proxima_abertura(local(2026, 12, 31, 19, 0)) == local(2027, 1, 2, 9, 0)


def test_datetime_em_outro_fuso_e_convertido_para_o_da_loja() -> None:
    sexta_1759_cg_em_utc = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    sexta_1800_cg_em_utc = datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
    assert LOJA.esta_aberta(sexta_1759_cg_em_utc) is True
    assert LOJA.esta_aberta(sexta_1800_cg_em_utc) is False
    proxima = LOJA.proxima_abertura(sexta_1800_cg_em_utc)
    assert proxima == local(2026, 1, 10, 9, 0)
    assert proxima.utcoffset() == timedelta(hours=-4)


def test_conversao_de_fuso_pode_mudar_o_dia() -> None:
    # Sábado 2026-01-10 02:00 em UTC+9 equivale a sexta 2026-01-09 13:00 em Campo Grande (UTC-4): aberta.
    toquio = datetime(2026, 1, 10, 2, 0, tzinfo=timezone(timedelta(hours=9)))
    assert LOJA.esta_aberta(toquio) is True


def test_datetime_ingenuo_e_rejeitado() -> None:
    ingenuo = datetime(2026, 1, 9, 10, 0)
    for operacao in (LOJA.esta_aberta, LOJA.proxima_abertura, LOJA.status):
        with pytest.raises(ErroDatetimeSemFuso):
            operacao(ingenuo)


def test_erro_de_fuso_e_erro_de_politicas_com_mensagem_em_portugues() -> None:
    with pytest.raises(ErroPoliticas, match="fuso horário"):
        LOJA.esta_aberta(datetime(2026, 1, 9, 10, 0))
