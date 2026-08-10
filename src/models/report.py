"""Esquema del informe de orientacion (ADR-019).

**La linea que separa este fichero en dos no es de estilo, es la decision D6.**

Un informe de orientacion tiene dos clases de contenido que se comportan de
forma opuesta:

- **Las cifras** —cuanto dura la carrera, cuanto se gana, cuanto cuesta, que
  porcentaje entra— salen del catalogo del MINEDU y del motor de afinidad. Son
  verificables y tienen que ser exactas.
- **La prosa** —el resumen del perfil y el por que de cada carrera— es lo que
  convierte una tabla en algo que un chico de dieciseis anos entiende. Ahi el
  modelo aporta lo que nadie mas puede aportar.

Si el modelo emitiera el documento entero, las cifras volverian a salir del
modelo. Y ese fallo ya ocurrio en este repositorio: el mock del frontend
atribuia al MINEDU numeros que el MINEDU nunca publico. Un informe que un
estudiante puede ensenar en casa para decidir donde estudiar no es sitio para
un numero plausible.

Por eso el modelo solo escribe dos campos —``profile_summary`` y el
``insight`` de cada carrera— y el resto lo rellena
``src.tools.report.handler``, que **vuelve a ejecutar el motor** en vez de
aceptar cifras por parametro. No es que al modelo se le pida que no invente:
es que no tiene por donde.

La metadata del informe como objeto guardado (id, usuario, fechas, claves de
S3, checksum) NO vive aqui: es de la fila del backend (fase 5 del ADR). Esto
es solo el contenido.
"""

from datetime import date

from pydantic import BaseModel, Field

# Version de la forma de este documento. Sube cuando un lector viejo dejaria
# de entender un informe nuevo: campo que desaparece, campo que cambia de
# tipo o de significado. Anadir un campo opcional no la mueve.
#
# Un informe se guarda en S3 y se relee meses despues, cuando el codigo que lo
# escribio ya no existe. Sin este numero, el que lo abra tiene que adivinar de
# que epoca es por que campos trae; con el, puede decidir.
SCHEMA_VERSION = "1"


class ReportCareer(BaseModel):
    """Una carrera del informe: cifras del motor, una frase del modelo."""

    # --- Del motor. El modelo no escribe ninguno de estos campos. ---
    career: str = Field(description="Nombre de la carrera, tal como viene del catalogo.")
    career_family: str = Field(description="Familia de carrera a la que pertenece.")
    riasec_profile: str = Field(description="Codigo RIASEC asignado a la carrera.")
    institution: str = Field(description="Institucion del programa mejor puntuado.")
    institution_type: str = Field(description="'Universidad' o 'Instituto'.")
    management_type: str = Field(description="'Publica' o 'Privada'.")
    location: str = Field(description="Departamento donde se dicta.")
    duration_years: float = Field(description="Duracion en anios.")
    monthly_income: float = Field(description="Ingreso mensual de egresados, en soles.")
    annual_cost: float = Field(description="Costo anual, en soles.")
    admission_rate: float = Field(description="Tasa de admision, 0-1.")
    match_score: float = Field(description="Puntuacion de Spark Match, 0-100.")
    score_breakdown: dict[str, float] = Field(
        description="De donde sale la puntuacion, por componente."
    )
    estimated: list[str] = Field(
        description=(
            "Campos que NO son datos medidos de este programa, sino la mediana "
            "de su familia de carrera. Viaja hasta el informe impreso a "
            "proposito: sin esta lista, un ingreso imputado es indistinguible "
            "de uno publicado."
        )
    )

    # --- Del modelo. ---
    insight: str = Field(
        description=(
            "Por que esta carrera encaja con este estudiante en concreto. "
            "Lo unico que el modelo escribe de cada carrera."
        )
    )


class OrientationReport(BaseModel):
    """El contenido de un informe de orientacion, listo para guardar."""

    # --- De la forma del documento, no de su contenido. ---
    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Version de la forma de este documento. Ver SCHEMA_VERSION.",
    )

    # --- Del modelo. ---
    profile_summary: str = Field(
        description=(
            "Retrato en prosa del perfil vocacional del estudiante. El otro "
            "de los dos campos que escribe el modelo."
        )
    )

    # --- Del motor y del perfil. ---
    riasec_code: str = Field(description="Codigo Holland de 3 letras del estudiante.")
    careers: list[ReportCareer] = Field(description="Carreras recomendadas, ya ordenadas.")
    total_candidates: int = Field(description="Cuantos programas del catalogo pasaron los filtros.")
    careers_matched: int = Field(description="Cuantas carreras distintas quedaron.")
    filters_applied: list[str] = Field(description="Que filtros se aplicaron de verdad.")
    candidates_without_each_filter: dict[str, int] = Field(
        description=(
            "Cuantos programas habria soltando cada filtro. Se guarda en el "
            "informe, no solo se dice en el chat: quien lo lea meses despues "
            "tiene que poder ver que el resultado dependia de unos filtros y "
            "cuanto recortaba cada uno."
        )
    )

    # --- Procedencia. Sin esto el informe no se puede releer con honestidad. ---
    scoring_version: str = Field(
        description=(
            "Version del criterio de puntuacion vigente al emitirlo. Un informe "
            "de hoy tiene que poder explicarse con las reglas de hoy aunque los "
            "pesos cambien manana."
        )
    )
    # Dos campos y no la etiqueta de una linea que se ensena en el chat. La
    # fila del backend guarda el origen y la fecha en columnas distintas
    # (ADR-019, D3), y la alternativa seria que el backend le sacara la fecha
    # a un texto libre con una expresion regular: el dia que la etiqueta
    # cambie de redaccion, la columna se llena de nulos sin que falle nada.
    dataset_source: str = Field(
        description="Catalogo de origen, sin fecha. Ej. 'Ponte en Carrera (MINEDU)'."
    )
    dataset_snapshot_date: date = Field(
        description="Fecha del corte de datos con el que se genero el informe."
    )


__all__ = ["SCHEMA_VERSION", "OrientationReport", "ReportCareer"]
