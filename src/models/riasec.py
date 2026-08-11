"""Los seis tipos de Holland, con su nombre en castellano.

Vivia dentro de `src/tools/assessment/handler.py` como `_TYPE_NAMES`, y con una
sola lectura estaba bien. Ahora hay dos: el cuestionario, que interpreta las
puntuaciones, y el informe en PDF, que despliega el codigo en la portada
--«IRA» no le dice nada a nadie, «Investigativo · Realista · Artistico» si--.

Copiar el diccionario habria funcionado hasta el dia en que alguien corrigiera
una tilde en un sitio y no en el otro, y el estudiante viera un nombre en el
chat y otro distinto en el documento que se lleva a casa. No es un fallo que
rompa nada: es de los que hacen dudar del resto.
"""

from __future__ import annotations

#: Letra Holland -> nombre del tipo. El orden es el canonico (RIASEC), no
#: alfabetico, porque es como se ensena y como se lee el codigo.
RIASEC_TYPE_NAMES: dict[str, str] = {
    "R": "Realista",
    "I": "Investigativo",
    "A": "Artístico",
    "S": "Social",
    "E": "Emprendedor",
    "C": "Convencional",
}

RIASEC_LETTERS = frozenset(RIASEC_TYPE_NAMES)


def riasec_type_name(letra: str) -> str:
    """El nombre del tipo, o la letra tal cual si no es una de las seis.

    Devolver la letra en vez de reventar es deliberado: el unico consumidor que
    puede encontrarse una letra rara es el informe, y ahi un `KeyError` seria
    quedarse sin PDF por un caracter suelto en un codigo que ya paso todas las
    validaciones de antes.
    """
    return RIASEC_TYPE_NAMES.get(letra.upper(), letra)


__all__ = ["RIASEC_LETTERS", "RIASEC_TYPE_NAMES", "riasec_type_name"]
