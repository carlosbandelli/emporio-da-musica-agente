"""`Montante`: valor monetário no formato entregue ao LLM (centavos exatos + texto já formatado no idioma)."""

from pydantic import BaseModel, ConfigDict

from emporio.modulos.comum.dinheiro import Dinheiro


class Montante(BaseModel):
    """Centavos inteiros e o mesmo valor formatado (`R$ 1.299,00` em pt-BR, `R$1,299.00` em en-US)."""

    model_config = ConfigDict(frozen=True)

    centavos: int
    texto: str


def montante(valor: Dinheiro, locale: str) -> Montante:
    """Converte `Dinheiro` para `Montante` no `locale` (ex.: `ctx.idioma.value`)."""
    return Montante(centavos=valor.centavos, texto=valor.formatar(locale))
