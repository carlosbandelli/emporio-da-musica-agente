from datetime import UTC, datetime, timedelta

from emporio.adaptadores.relogio import FUSO_CAMPO_GRANDE, RelogioSistema


class Fonte:
    def __init__(self) -> None:
        self.t = datetime(2030, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def test_sem_referencia_usa_hora_real() -> None:
    antes = datetime.now(UTC)
    agora = RelogioSistema().agora()
    assert agora.tzinfo is not None
    assert antes <= agora <= datetime.now(UTC) + timedelta(seconds=1)


def test_referencia_com_fuso_avanca_com_o_relogio() -> None:
    fonte = Fonte()
    rel = RelogioSistema(datetime(2026, 3, 10, 14, 0, tzinfo=UTC), fonte=fonte)
    assert rel.agora() == datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
    fonte.t += timedelta(minutes=5)
    assert rel.agora() == datetime(2026, 3, 10, 14, 5, tzinfo=UTC)
    assert rel.agora().utcoffset() == timedelta(0)


def test_referencia_ingenua_e_hora_de_campo_grande() -> None:
    rel = RelogioSistema(datetime(2026, 3, 10, 9, 0), fonte=Fonte())
    assert rel.agora() == datetime(2026, 3, 10, 13, 0, tzinfo=UTC)
    assert rel.agora().astimezone(FUSO_CAMPO_GRANDE).hour == 9
