"""`RelogioSistema`: implementa a porta `Relogio` com relógio real ou simulado a partir de uma data de referência."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

FUSO_CAMPO_GRANDE = ZoneInfo("America/Campo_Grande")


class RelogioSistema:
    """Devolve o instante atual (UTC, com fuso).

    Com `data_referencia`, o tempo *começa* nela e avança com o relógio real (deslocamento fixo calculado na
    criação), para simular "hoje" em testes e exemplos. Referência sem fuso é interpretada em Campo Grande.
    """

    def __init__(
        self,
        data_referencia: datetime | None = None,
        *,
        fonte: Callable[[], datetime] | None = None,
    ) -> None:
        self._fonte = fonte or (lambda: datetime.now(UTC))
        if data_referencia is None:
            self._deslocamento = timedelta(0)
            return
        referencia = data_referencia
        if referencia.tzinfo is None:
            referencia = referencia.replace(tzinfo=FUSO_CAMPO_GRANDE)
        self._deslocamento = referencia - self._fonte()

    def agora(self) -> datetime:
        """Instante atual com fuso (UTC)."""
        return (self._fonte() + self._deslocamento).astimezone(UTC)
