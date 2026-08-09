"""Carga el catalogo real de programas de Ponte en Carrera.

6208 combinaciones carrera-institucion del portal del MINEDU: 554 carreras,
1071 instituciones, los 25 departamentos. Con duracion, ingreso mensual de
los egresados, costo anual y tasa de admision.

El fichero lo genera ``scripts/build_programs_dataset.py`` desde
``spark-match-05-data-pipeline``; la procedencia y sus limites estan en
``data/programs/README.md``. Aqui solo se lee.

**Lo que hay que entender antes de tocar esto**: la mayoria de las cifras
numericas del dataset NO son mediciones de ese programa concreto. El
pipeline imputa lo que falta con la mediana de la familia de carrera, y eso
afecta al 73% de los ingresos y al 65% de las tasas de admision. Por eso
cada fila trae cuatro banderas ``*_measured`` y por eso el handler devuelve
la lista de campos estimados en cada resultado: decirle a un estudiante
"en esta universidad vas a ganar S/ 1442" cuando ese numero es la mediana
de su familia seria inventarselo con formato de dato oficial.
"""

from __future__ import annotations

import csv
import logging
import unicodedata
from collections import Counter
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# Resuelto desde la raiz del repositorio, igual que el catalogo de carreras,
# para que funcione tanto en local como dentro del contenedor.
DATASET_PATH = Path(__file__).resolve().parents[3] / "data" / "programs" / "programs.csv"

# Fecha del snapshot del portal. Viaja en cada respuesta de la herramienta:
# el portal del MINEDU devuelve HTTP 500 desde el 2026-07-12 y la etapa de
# ingesta esta congelada, asi que este dato no se refresca solo y decir
# "datos oficiales" a secas seria omitir que tienen fecha.
SNAPSHOT_DATE = "2026-06-13"
SOURCE_LABEL = f"Ponte en Carrera (MINEDU), datos del {SNAPSHOT_DATE}"


class Program(TypedDict):
    """Una combinacion carrera-institucion del portal."""

    source_id: str
    career: str
    career_family: str
    riasec_profile: str
    institution: str
    institution_type: str
    management_type: str
    location: str
    duration_years: float
    monthly_income: float
    annual_cost: float
    admission_rate: float
    # Que cifras se midieron de verdad. Las demas son la mediana de la
    # familia de carrera, no un dato de este programa.
    duration_measured: bool
    income_measured: bool
    cost_measured: bool
    admission_measured: bool
    # Precalculado al cargar: comparar acentos en cada busqueda sobre 6208
    # filas es trabajo repetido que no cambia nunca.
    search_text: str


_CACHE: list[Program] | None = None


def normalize(text: str) -> str:
    """Minusculas y sin acentos, para comparar lo que escribe una persona.

    Un estudiante escribe "ingenieria de sistemas" o "Áncash" indistintamente
    y el dataset guarda "Ingeniería de Sistemas" y "Áncash". Comparar en
    crudo convierte una tilde en un cero resultados.
    """
    stripped = unicodedata.normalize("NFD", text.casefold())
    return "".join(char for char in stripped if unicodedata.category(char) != "Mn")


def _to_float(raw: str) -> float:
    try:
        return float(raw)
    except TypeError, ValueError:
        return 0.0


def _to_bool(raw: str) -> bool:
    return raw.strip().lower() == "true"


def _parse_row(row: dict[str, str]) -> Program:
    # Campo a campo, sin bucles sobre nombres: un TypedDict solo admite
    # claves literales, y escribirlo asi hace que anadir una columna al CSV
    # sin declararla en `Program` falle al comprobar tipos, no en produccion.
    return Program(
        source_id=row["source_id"],
        career=row["career"],
        career_family=row["career_family"],
        riasec_profile=row["riasec_profile"].upper(),
        institution=row["institution"],
        institution_type=row["institution_type"],
        management_type=row["management_type"],
        location=row["location"],
        duration_years=_to_float(row["duration_years"]),
        monthly_income=_to_float(row["monthly_income"]),
        annual_cost=_to_float(row["annual_cost"]),
        admission_rate=_to_float(row["admission_rate"]),
        duration_measured=_to_bool(row["duration_measured"]),
        income_measured=_to_bool(row["income_measured"]),
        cost_measured=_to_bool(row["cost_measured"]),
        admission_measured=_to_bool(row["admission_measured"]),
        search_text=normalize(f"{row['career']} {row['career_family']} {row['institution']}"),
    )


def load_programs(path: Path | None = None) -> list[Program]:
    """Lee el catalogo (una vez) y lo devuelve.

    Una lista vacia cuando el fichero no existe: el handler lo trata como
    error explicito en vez de devolver "sin resultados", que es lo que se
    veria si un despliegue se dejara el CSV fuera de la imagen.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    dataset = path or DATASET_PATH
    if not dataset.exists():
        logger.warning("El catalogo de programas no existe en %s", dataset)
        _CACHE = []
        return _CACHE

    with dataset.open(encoding="utf-8", newline="") as handle:
        _CACHE = [_parse_row(row) for row in csv.DictReader(handle)]

    logger.info("Catalogo de programas cargado: %d filas de %s", len(_CACHE), dataset)
    return _CACHE


def reload_programs(path: Path | None = None) -> list[Program]:
    """Limpia la cache y vuelve a leer. Para tests."""
    global _CACHE, _CAREERS_CACHE
    _CACHE = None
    # La vista por carrera se deriva de esta, asi que una sin la otra dejaria
    # las 554 carreras describiendo un CSV que ya no esta cargado.
    _CAREERS_CACHE = None
    return load_programs(path)


###############################################################################
# Vista por carrera
#
# `programs.csv` es carrera x institucion: "Ingenieria de Sistemas" aparece 53
# veces, una por universidad que la ofrece. Para hablar de vocacion --que
# carreras existen, cual encaja con un perfil RIASEC-- esa repeticion no aporta
# y estorba: un top-5 de afinidad sacado de las 6.208 filas devuelve la misma
# carrera cinco veces en cinco universidades distintas.
#
# Esta vista colapsa el CSV a las 554 carreras unicas. NO es otra fuente de
# datos: es una proyeccion de la misma, lo cual es justamente el motivo de que
# viva aqui, junto a `load_programs`, y no en un modulo con su propio fichero
# --que es lo que habia antes (`data/careers/*.md`, 20 fichas curadas a mano) y
# lo que garantizaba que las dos versiones de "carrera" se separasen con el
# tiempo.
###############################################################################


class CareerEntry(TypedDict):
    """Una carrera unica del catalogo, sin la dimension institucional."""

    career: str
    career_family: str
    riasec_profile: str
    # En cuantos programas (institucion x sede) se puede estudiar. Ordena los
    # resultados: una carrera que se ofrece en 117 sitios es una respuesta mas
    # util a "que estudio" que otra que solo existe en uno.
    program_count: int
    search_text: str


_CAREERS_CACHE: list[CareerEntry] | None = None


def _mas_frecuente(cuentas: Counter[str]) -> str:
    """El valor mas repetido; a igualdad, el menor alfabeticamente.

    El desempate alfabetico no es cosmetico: sin el, el resultado depende del
    orden de insercion del `Counter` y dos cargas del mismo CSV podrian
    devolver familias distintas para la misma carrera.
    """
    return min(cuentas.items(), key=lambda par: (-par[1], par[0]))[0]


def load_careers(path: Path | None = None) -> list[CareerEntry]:
    """Colapsa el catalogo de programas a sus carreras unicas.

    `career_family` y `riasec_profile` se resuelven por moda, no tomando la
    primera fila. Para el RIASEC hoy es un no-op --comprobado el 2026-08-09:
    las 554 carreras tienen exactamente un codigo cada una, porque
    `riasec_tagging.py` etiqueta por carrera y propaga con un merge-- pero la
    familia SI varia: el propio pipeline documenta que "a handful of careers
    appear under more than one family". Resolverlo igual en los dos sitios
    (misma regla que `load_unique_careers` en 05-data-pipeline) evita que el
    agente y el pipeline discrepen sobre la familia de una carrera.
    """
    global _CAREERS_CACHE
    if _CAREERS_CACHE is not None:
        return _CAREERS_CACHE

    familias: dict[str, Counter[str]] = {}
    perfiles: dict[str, Counter[str]] = {}
    cuenta: Counter[str] = Counter()

    for program in load_programs(path):
        career = program["career"]
        familias.setdefault(career, Counter())[program["career_family"]] += 1
        perfiles.setdefault(career, Counter())[program["riasec_profile"]] += 1
        cuenta[career] += 1

    carreras = [
        CareerEntry(
            career=career,
            career_family=_mas_frecuente(familias[career]),
            riasec_profile=_mas_frecuente(perfiles[career]),
            program_count=cuenta[career],
            search_text=normalize(f"{career} {_mas_frecuente(familias[career])}"),
        )
        for career in cuenta
    ]
    carreras.sort(key=lambda entry: entry["career"])

    _CAREERS_CACHE = carreras
    logger.info("Vista por carrera construida: %d carreras unicas", len(carreras))
    return _CAREERS_CACHE


def reload_careers(path: Path | None = None) -> list[CareerEntry]:
    """Limpia las dos caches y reconstruye la vista. Para tests."""
    global _CAREERS_CACHE
    _CAREERS_CACHE = None
    reload_programs(path)
    return load_careers(path)


__all__ = [
    "DATASET_PATH",
    "SNAPSHOT_DATE",
    "SOURCE_LABEL",
    "CareerEntry",
    "Program",
    "load_careers",
    "load_programs",
    "normalize",
    "reload_careers",
    "reload_programs",
]
