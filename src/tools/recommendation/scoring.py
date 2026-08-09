"""Puntuacion multicriterio de un programa para un perfil RIASEC.

Este modulo es **el unico sitio donde se define "encaja"**. Es a proposito, y
recoge la advertencia que ya llevaba `src/tools/programs/handler.py`: repartir
esa definicion entre la herramienta de recuperacion y la de afinidad las
dejaria separarse con el tiempo. Por eso `search_programs` sigue siendo
recuperacion pura, `calculate_affinity` sigue siendo afinidad RIASEC a nivel de
carrera, y la combinacion de las dos con la economia vive aqui.

Tres decisiones que conviene entender antes de tocar los pesos:

**1. Los filtros no puntuan, excluyen.** Region, gestion, tipo de institucion y
presupuesto se aplican antes de puntuar (ver `handler.py`). Un estudiante que
pide Arequipa publica no debe ver Piura privada con menos puntos: debe no
verla. Meter esos criterios en el score los convertiria en preferencias
negociables, y no lo son.

**2. Una cifra imputada no puntua.** El pipeline rellena lo que falta con la
mediana de la familia de carrera -- afecta al 73% de los ingresos y al 65% de
las tasas de admision -- y cada fila trae su bandera `*_measured`. Un ingreso
imputado no dice nada de ESE programa, asi que su componente vale
`NEUTRO` (0.5) en lugar del valor normalizado: ni ayuda ni penaliza. Dejarlo
puntuar seria construir un ranking sobre medianas de familia presentadas como
datos del programa, que es exactamente el fallo que este repositorio lleva
arrastrando desde el mock del frontend.

**3. Los rangos de referencia son fijos, no relativos al resultado.** Se
normaliza contra percentiles del dataset completo y no contra el minimo y el
maximo de los candidatos filtrados. Con min-max sobre el subconjunto, anadir un
candidato cambiaria la nota de todos los demas y un 0.8 significaria una cosa
distinta en cada busqueda. Con rangos fijos, un 0.8 significa siempre lo mismo.
"""

from __future__ import annotations

from typing import Any

from src.tools.programs.loader import Program

# Version del criterio de puntuacion. Viaja en cada respuesta y se guarda en el
# informe: un informe emitido hoy tiene que poder explicarse con las reglas de
# hoy aunque los pesos cambien manana.
SCORING_VERSION = "1.0.0"

# El peso de la afinidad domina porque el producto es orientacion VOCACIONAL:
# su premisa es que encajar importa mas que cobrar. Si algun dia se decide lo
# contrario, se cambia aqui, se sube SCORING_VERSION y los informes viejos
# siguen siendo interpretables.
WEIGHTS: dict[str, float] = {
    "riasec_affinity": 0.50,
    "income": 0.20,
    "admission_accessibility": 0.20,
    "affordability": 0.10,
}

# Percentiles 5 y 95 calculados el 2026-08-09 sobre `data/programs/programs.csv`,
# usando SOLO las filas con la bandera de medicion correspondiente en true
# (1.680 ingresos, 3.112 costos, 2.160 tasas de admision de las 6.208 filas).
# Se excluyen las imputadas a proposito: son medianas de familia y comprimen la
# distribucion, con lo que el rango saldria mas estrecho de lo que es.
#
# p5/p95 y no min/max: el maximo de `annual_cost` son S/ 32.530, un caso
# extremo que aplastaria a todos los demas contra el cero.
REFERENCE_RANGES: dict[str, tuple[float, float]] = {
    "monthly_income": (1598.8, 4195.0),
    "annual_cost": (52.0, 6680.0),
    "admission_rate": (9.0, 89.0),
}

# Lo que vale un componente cuya cifra esta imputada: ni bien ni mal.
NEUTRO = 0.5


def _normalizar(valor: float, minimo: float, maximo: float, *, invertir: bool = False) -> float:
    """Lleva `valor` a [0, 1] contra un rango fijo, recortando los extremos.

    `invertir` para las magnitudes donde menos es mejor (el costo): un programa
    de S/ 52 al ano tiene que puntuar 1.0 en asequibilidad, no 0.0.
    """
    if maximo <= minimo:
        return NEUTRO
    escalado = (valor - minimo) / (maximo - minimo)
    acotado = max(0.0, min(1.0, escalado))
    return 1.0 - acotado if invertir else acotado


def _componente(
    valor: float,
    medido: bool,
    rango: tuple[float, float],
    *,
    invertir: bool = False,
) -> float:
    """Un componente economico, o `NEUTRO` si la cifra no se midio."""
    if not medido:
        return NEUTRO
    return _normalizar(valor, rango[0], rango[1], invertir=invertir)


def score_program(program: Program, riasec_similarity: float) -> dict[str, Any]:
    """Puntua un programa ya filtrado.

    Args:
        program: Una fila de `load_programs()`.
        riasec_similarity: Afinidad 0-100 devuelta por
            ``src.tools.matching.handler._riasec_similarity``. Se pasa ya
            calculada para no duplicar la formula: la afinidad se define en
            `matching`, aqui solo se pondera.

    Returns:
        ``{"match_score": 0-100, "breakdown": {componente: 0-1}}``.

    Nota sobre `admission_accessibility`: puntua ALTO la tasa de admision alta,
    o sea lo accesible, no lo selectivo. Es un juicio de valor y se deja
    explicito: la herramienta existe para que un estudiante encuentre caminos
    viables, no para ordenar universidades por prestigio. Un programa donde
    entra el 9% no es peor, es menos alcanzable, y quien quiera justo ese lo
    tiene igual en la lista con su cifra a la vista.

    `duration_years` NO entra en el score. Se informa, pero puntuarla exigiria
    decidir que menos anos es mejor, y eso no es cierto para nadie en general.
    """
    breakdown = {
        "riasec_affinity": max(0.0, min(1.0, riasec_similarity / 100.0)),
        "income": _componente(
            program["monthly_income"],
            program["income_measured"],
            REFERENCE_RANGES["monthly_income"],
        ),
        "admission_accessibility": _componente(
            program["admission_rate"],
            program["admission_measured"],
            REFERENCE_RANGES["admission_rate"],
        ),
        "affordability": _componente(
            program["annual_cost"],
            program["cost_measured"],
            REFERENCE_RANGES["annual_cost"],
            invertir=True,
        ),
    }

    total = sum(breakdown[nombre] * peso for nombre, peso in WEIGHTS.items())

    return {
        "match_score": round(total * 100, 1),
        "breakdown": {nombre: round(valor, 3) for nombre, valor in breakdown.items()},
    }


__all__ = [
    "NEUTRO",
    "REFERENCE_RANGES",
    "SCORING_VERSION",
    "WEIGHTS",
    "score_program",
]
