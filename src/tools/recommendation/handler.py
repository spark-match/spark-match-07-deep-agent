"""Recomendacion multicriterio: filtros duros + afinidad + economia.

Combina las tres piezas que ya existian por separado:

- ``load_programs`` (``src.tools.programs.loader``): las 6.208 filas reales.
- ``_riasec_similarity`` (``src.tools.matching.handler``): la afinidad.
- ``score_program`` (``.scoring``): la ponderacion.

Y anade lo que faltaba, que es lo que hacia que los filtros de ``/filters``
--region, gestion, tipo de institucion y presupuesto-- no influyeran en nada:
aqui **excluyen** antes de puntuar.

**Una carrera por resultado.** El ranking es de programas, porque la ficha del
informe necesita institucion y cifras, pero se queda solo el mejor programa de
cada carrera. Sin eso un top-3 saldria con "Ingenieria de Sistemas" en tres
universidades y el estudiante veria una opcion creyendo que ve tres.

**Ojo con los dos "tipos", que van cruzados respecto al frontend**:

    frontend `institutionType` (publica/privada)   -> CSV `management_type`
    frontend `academicType`    (universidad/inst.) -> CSV `institution_type`

Los parametros de aqui usan los nombres del CSV, que es el dato, igual que
``search_programs``. Quien conecte el frontend tiene que cruzarlos.

Esquema de retorno, el mismo que el resto de handlers:

    {
        "status": "success" | "error",
        "data": {"recommendations": [...], "total_candidates": int,
                 "filters_applied": {...}, "scoring_version": str,
                 "source": str} | None,
        "errors": [<mensaje>] | None,
    }
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.tools.matching.handler import _riasec_similarity
from src.tools.programs.loader import SOURCE_LABEL, Program, load_programs, normalize
from src.tools.recommendation.scoring import SCORING_VERSION, score_program

MAX_TOP_N = 10
DEFAULT_TOP_N = 3

# Valores que significan "no filtres por esto". El frontend manda "ambas" para
# la gestion y "ambos" para el tipo de institucion; se aceptan los dos en los
# dos sitios para que un cruce mal hecho no filtre por error.
_SIN_FILTRO = frozenset({"ambas", "ambos", "todas", "todos", "cualquiera", ""})


def _valor_filtro(bruto: str | None) -> str | None:
    """Normaliza el valor de un filtro; None si no hay que filtrar.

    Normalizar resuelve de paso el mapeo de vocabulario: el frontend manda
    "publica" y el CSV guarda "Publica" -- sin acentos y en minusculas son la
    misma cadena, asi que no hace falta una tabla de traduccion que mantener.
    """
    if bruto is None:
        return None
    normalizado = normalize(bruto.strip())
    return None if normalizado in _SIN_FILTRO else normalizado


def _igual_normalizado(
    accesor: Callable[[Program], str],
    valor: str,
) -> Callable[[Program], bool]:
    """Predicado de igualdad sobre un campo de texto, sin acentos ni mayusculas.

    Se construye con una fabrica y no con `lambda p, v=valor: ...` porque ese
    truco de argumento por defecto -- la forma habitual de capturar el valor en
    un bucle -- deja a mypy sin poder inferir el tipo del lambda.
    """

    def predicado(program: Program) -> bool:
        return normalize(accesor(program)) == valor

    return predicado


def _tope_de_costo(maximo: float) -> Callable[[Program], bool]:
    def predicado(program: Program) -> bool:
        return program["annual_cost"] <= maximo

    return predicado


def _construir_filtros(
    region: str | None,
    management_type: str | None,
    institution_type: str | None,
    max_annual_cost: float | None,
) -> dict[str, Callable[[Program], bool]]:
    """Solo los filtros que se pidieron de verdad, por nombre."""
    filtros: dict[str, Callable[[Program], bool]] = {}

    if (valor := _valor_filtro(region)) is not None:
        filtros["region"] = _igual_normalizado(lambda p: p["location"], valor)

    if (valor := _valor_filtro(management_type)) is not None:
        filtros["management_type"] = _igual_normalizado(lambda p: p["management_type"], valor)

    if (valor := _valor_filtro(institution_type)) is not None:
        filtros["institution_type"] = _igual_normalizado(lambda p: p["institution_type"], valor)

    if max_annual_cost is not None and max_annual_cost > 0:
        filtros["max_annual_cost"] = _tope_de_costo(float(max_annual_cost))

    return filtros


def _candidatos_sin_cada_filtro(
    catalog: list[Program],
    filtros: dict[str, Callable[[Program], bool]],
) -> dict[str, int]:
    """Cuantos programas habria soltando cada filtro, uno a uno.

    Se calcula SIEMPRE, no solo cuando el resultado sale vacio, y ese es el
    punto. Un filtro duro no devuelve una respuesta equivocada: borra opciones
    en silencio. Si el presupuesto que el agente infirio de la conversacion es
    S/ 2.000 en vez de S/ 20.000, la busqueda sigue devolviendo resultados
    perfectamente plausibles y nadie se entera de que el estudiante se quedo
    sin ver el 90% del catalogo.

    Con esto, el agente puede decir "con tu presupuesto quedan 43 de los 411 de
    Arequipa" y el estudiante corrige si eso no era lo que quiso decir. Convierte
    un error invisible en uno visible, que es lo unico que se puede corregir.

    Coste: O(n x k). Con 6.208 filas y 4 filtros son ~25.000 evaluaciones de
    predicado, despreciable frente a la puntuacion que viene despues.
    """
    return {
        nombre: sum(
            1 for p in catalog if all(f(p) for clave, f in filtros.items() if clave != nombre)
        )
        for nombre in filtros
    }


def _filtro_mas_restrictivo(impacto: dict[str, int]) -> tuple[str, int] | None:
    """Cual soltar cuando no queda nada: el que mas abre.

    Decirle a un estudiante "no hay resultados" y callarse es dejarlo tocando
    los cuatro controles a ciegas.
    """
    utiles = {nombre: n for nombre, n in impacto.items() if n > 0}
    if not utiles:
        return None
    nombre = max(utiles, key=lambda k: utiles[k])
    return nombre, utiles[nombre]


def _estimados(program: Program) -> list[str]:
    """Los campos cuyo valor NO se midio en este programa.

    Mismo criterio que `search_programs`: sin esta lista, un ingreso imputado
    (la mediana de la familia de carrera) es indistinguible de uno real.
    """
    return [
        campo
        for campo, medido in (
            ("duration_years", program["duration_measured"]),
            ("monthly_income", program["income_measured"]),
            ("annual_cost", program["cost_measured"]),
            ("admission_rate", program["admission_measured"]),
        )
        if not medido
    ]


def _a_resultado(program: Program, puntuacion: dict[str, Any]) -> dict[str, Any]:
    return {
        "career": program["career"],
        "career_family": program["career_family"],
        "riasec_profile": program["riasec_profile"],
        "institution": program["institution"],
        "institution_type": program["institution_type"],
        "management_type": program["management_type"],
        "location": program["location"],
        "duration_years": program["duration_years"],
        "monthly_income": program["monthly_income"],
        "annual_cost": program["annual_cost"],
        "admission_rate": program["admission_rate"],
        "estimated": _estimados(program),
        "match_score": puntuacion["match_score"],
        "score_breakdown": puntuacion["breakdown"],
    }


def recommend_programs_handler(
    riasec_code: str,
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Recomienda programas reales para un perfil, respetando los filtros.

    Args:
        riasec_code: Codigo Holland de 3 letras del estudiante.
        region: Departamento. Se compara sin acentos ni mayusculas.
        management_type: "publica" | "privada". "ambas" o None = sin filtro.
        institution_type: "universidad" | "instituto". "ambos" o None = sin filtro.
        max_annual_cost: Presupuesto anual maximo en soles.
        top_n: Cuantas recomendaciones, una por carrera. Maximo `MAX_TOP_N`.
    """
    if not isinstance(riasec_code, str) or not riasec_code.strip():
        return {
            "status": "error",
            "data": None,
            "errors": ["riasec_code must be a non-empty string"],
        }

    codigo = riasec_code.upper().strip()[:3]
    top_n = max(1, min(int(top_n) if top_n else DEFAULT_TOP_N, MAX_TOP_N))

    catalog = load_programs()
    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["el catalogo de programas esta vacio - revisa data/programs/"],
        }

    filtros = _construir_filtros(region, management_type, institution_type, max_annual_cost)
    candidatos = [p for p in catalog if all(f(p) for f in filtros.values())]
    impacto = _candidatos_sin_cada_filtro(catalog, filtros)

    if not candidatos:
        sugerencia = _filtro_mas_restrictivo(impacto)
        detalle = (
            f" Soltando '{sugerencia[0]}' aparecerian {sugerencia[1]} programas."
            if sugerencia
            else ""
        )
        return {
            "status": "error",
            "data": None,
            "errors": [f"Ningun programa del catalogo cumple los filtros pedidos.{detalle}"],
        }

    # Una entrada por carrera, la de mejor puntuacion. Se recorre entero y se
    # guarda el mejor en vez de ordenar y deduplicar despues: el resultado es el
    # mismo y no hace falta materializar 6.208 puntuaciones ordenadas.
    mejor_por_carrera: dict[str, dict[str, Any]] = {}
    for program in candidatos:
        puntuacion = score_program(program, _riasec_similarity(codigo, program["riasec_profile"]))
        resultado = _a_resultado(program, puntuacion)
        actual = mejor_por_carrera.get(program["career"])
        if actual is None or _orden(resultado) < _orden(actual):
            mejor_por_carrera[program["career"]] = resultado

    ranking = sorted(mejor_por_carrera.values(), key=_orden)

    return {
        "status": "success",
        "data": {
            "recommendations": ranking[:top_n],
            "total_candidates": len(candidatos),
            "careers_matched": len(mejor_por_carrera),
            "filters_applied": sorted(filtros),
            # Cuantos programas habria soltando cada filtro. Sirve para que el
            # agente pueda decir cuanto recorta cada uno en vez de presentar el
            # resultado como si fuera todo el catalogo. Ver
            # `_candidatos_sin_cada_filtro`.
            "candidates_without_each_filter": impacto,
            "riasec_code": codigo,
            "scoring_version": SCORING_VERSION,
            "source": SOURCE_LABEL,
        },
        "errors": None,
    }


def _orden(resultado: dict[str, Any]) -> tuple[float, int, str, str]:
    """Puntuacion desc; a igualdad, mas cifras medidas, luego alfabetico.

    El segundo criterio no es cosmetico: dos programas pueden empatar
    justamente porque a uno le faltan cifras y sus componentes valen el neutro
    0.5. Entre esos dos, delante va el que si se midio -- lo que mejor sabemos
    antes que lo que estimamos, el mismo criterio que ya usa `search_programs`.
    """
    return (
        -resultado["match_score"],
        len(resultado["estimated"]),
        resultado["career"],
        resultado["institution"],
    )


__all__ = ["DEFAULT_TOP_N", "MAX_TOP_N", "recommend_programs_handler"]
