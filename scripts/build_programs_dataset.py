"""Construye ``data/programs/programs.csv`` desde el pipeline de datos.

Fuente: ``spark-match-05-data-pipeline``, ficheros ``data/features.csv`` y
``data/riasec_tags.csv``. Este script existe para que el CSV que vive en este
repositorio sea **reproducible y auditable**, no un fichero caido del cielo:
cualquiera puede volver a generarlo y comparar.

    uv run python scripts/build_programs_dataset.py ../spark-match-05-data-pipeline

Que hace, y por que:

1. **Se queda con las columnas de "consultas y explicacion de resultados"**
   del diccionario de datos del pipeline, mas los flags de imputacion. Las
   normalizadas (``*_norm``) se descartan: el propio diccionario dice que no
   deben usarse para explicarle nada al usuario, porque son transformaciones
   matematicas sin lectura directa.

2. **Usa las versiones imputadas** (``monthly_income_imputed`` y compania),
   que son los valores finales del sistema. Las originales quedan fuera.

3. **Invierte los flags**: ``*_imputed_flag`` pasa a ``*_measured``. La
   polaridad importa. Lo que hay que preguntarse antes de ensenarle una
   cifra a un estudiante es "¿esto se midio?", y la respuesta tiene que
   leerse sin darle la vuelta mentalmente.

4. **Cruza el perfil RIASEC** por nombre de carrera. Las 554 carreras del
   dataset tienen etiqueta, y las 554 vienen de ``llm_tagged``: las puso un
   modelo, no el MINEDU. Por eso viaja aparte y por eso el README lo dice.

5. **Limpia el espacio en blanco**: la columna ``location`` del pipeline
   trae relleno a la derecha ("Cajamarca    ").

Los dos CSV de origen llevan BOM, asi que se leen con ``utf-8-sig``. Con
``utf-8`` a secas la primera columna pasa a llamarse ``﻿id`` y el cruce
falla de una forma que no se ve hasta que revienta.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Columnas de salida, en orden. El nombre es el que va a leer un modelo, asi
# que se prefiere el corto y sin sufijos internos: `monthly_income`, no
# `monthly_income_imputed`.
FIELDNAMES = [
    "source_id",
    "career",
    "career_family",
    "riasec_profile",
    "institution",
    "institution_type",
    "management_type",
    "location",
    "duration_years",
    "monthly_income",
    "annual_cost",
    "admission_rate",
    "duration_measured",
    "income_measured",
    "cost_measured",
    "admission_measured",
]

# (columna de salida, columna imputada de origen, flag de imputacion)
_NUMERIC = [
    ("duration_years", "duration_years_imputed", "duration_imputed_flag", "duration_measured"),
    ("monthly_income", "monthly_income_imputed", "monthly_income_imputed_flag", "income_measured"),
    ("annual_cost", "annual_cost_imputed", "annual_cost_imputed_flag", "cost_measured"),
    (
        "admission_rate",
        "admission_rate_imputed",
        "admission_rate_imputed_flag",
        "admission_measured",
    ),
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(raw: str) -> str:
    """Deja el numero en la forma mas corta que no pierda informacion.

    El pipeline escribe todo como float ("5.0", "1442.0"). Un entero se
    escribe como entero: son ~6 kB menos en el fichero y, sobre todo, es lo
    que un modelo va a repetir literalmente en su respuesta -- "dura 5 anos"
    lee mejor que "dura 5.0 anos".
    """
    try:
        value = float(raw)
    except TypeError, ValueError:
        return ""
    return str(int(value)) if value.is_integer() else str(round(value, 2))


def build_rows(
    features: list[dict[str, str]], riasec: list[dict[str, str]]
) -> list[dict[str, str]]:
    profiles = {row["career"].strip(): row["riasec_profile"].strip() for row in riasec}

    rows: list[dict[str, str]] = []
    for source in features:
        career = source["career"].strip()
        row = {
            "source_id": source["id"].strip(),
            "career": career,
            "career_family": source["career_family"].strip(),
            "riasec_profile": profiles.get(career, ""),
            "institution": source["institution"].strip(),
            "institution_type": source["institution_type"].strip(),
            "management_type": source["management_type"].strip(),
            "location": source["location"].strip(),
        }
        for out_name, imputed_col, flag_col, measured_name in _NUMERIC:
            row[out_name] = _number(source[imputed_col])
            # El flag del pipeline dice si SE IMPUTO; aqui se guarda si se
            # MIDIO, que es la pregunta que importa antes de ensenar la cifra.
            row[measured_name] = "false" if source[flag_col].strip() == "True" else "true"
        rows.append(row)

    # Orden estable para que dos generaciones del mismo origen den el mismo
    # fichero byte a byte, y un diff signifique que cambio el dato.
    rows.sort(key=lambda r: (r["career"], r["institution"], r["location"], r["source_id"]))
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    pipeline = Path(argv[1])
    features_path = pipeline / "data" / "features.csv"
    riasec_path = pipeline / "data" / "riasec_tags.csv"
    for path in (features_path, riasec_path):
        if not path.exists():
            print(f"no existe: {path}", file=sys.stderr)
            return 1

    rows = build_rows(_read_csv(features_path), _read_csv(riasec_path))

    destination = Path(__file__).resolve().parents[1] / "data" / "programs" / "programs.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    sin_perfil = sum(1 for row in rows if not row["riasec_profile"])
    print(f"escritas {len(rows)} filas en {destination}")
    print(f"  carreras distintas:    {len({r['career'] for r in rows})}")
    print(f"  instituciones:         {len({r['institution'] for r in rows})}")
    print(f"  departamentos:         {len({r['location'] for r in rows})}")
    print(f"  filas sin perfil RIASEC: {sin_perfil}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
