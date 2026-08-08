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
    global _CACHE
    _CACHE = None
    return load_programs(path)


__all__ = [
    "DATASET_PATH",
    "SNAPSHOT_DATE",
    "SOURCE_LABEL",
    "Program",
    "load_programs",
    "normalize",
    "reload_programs",
]
