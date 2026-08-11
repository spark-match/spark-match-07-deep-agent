"""Un informe de ejemplo para las pruebas de renderizado.

Se construye a mano en vez de pedirselo al motor: lo que se prueba aqui es el
RENDERIZADO, y hacerlo depender del catalogo real ataria estos tests a los
datos del MINEDU. Si manana cambia el CSV, deben romperse los tests del motor,
no los de la maquetacion.
"""

from __future__ import annotations

from datetime import date

from src.models.report import OrientationReport, ReportCareer

BREAKDOWN = {
    "riasec_affinity": 0.91,
    "income": 0.62,
    "admission_accessibility": 0.18,
    "affordability": 0.97,
}


def carrera(**overrides: object) -> ReportCareer:
    base: dict[str, object] = {
        "career": "Ingeniería Civil",
        "career_family": "Ingeniería",
        "riasec_profile": "IRC",
        "institution": "Universidad Nacional de San Agustín",
        "institution_type": "Universidad",
        "management_type": "Pública",
        "location": "Arequipa",
        "duration_years": 5.0,
        "monthly_income": 4261.0,
        "annual_cost": 50.0,
        # 0-100, como en el catalogo. Ver la descripcion del campo en
        # `ReportCareer`: el contrato decia 0-1 y el dato nunca lo fue.
        "admission_rate": 3.0,
        "match_score": 87.4,
        "score_breakdown": dict(BREAKDOWN),
        "estimated": [],
        "insight": "Te interesa entender por qué falla algo antes de arreglarlo.",
    }
    base.update(overrides)
    return ReportCareer(**base)  # type: ignore[arg-type]


def informe(**overrides: object) -> OrientationReport:
    base: dict[str, object] = {
        "profile_summary": (
            "Se te da bien entender cómo funcionan las cosas y te cansa lo "
            "repetitivo, pero disfrutas explicándoselo a otros."
        ),
        "riasec_code": "IRC",
        "careers": [
            carrera(),
            carrera(
                career="Química Industrial",
                institution="Universidad Católica de Santa María",
                management_type="Privada",
                monthly_income=3590.0,
                annual_cost=280.0,
                admission_rate=19.0,
                match_score=71.2,
                estimated=["monthly_income"],
                insight="Comparte el método pero con más laboratorio.",
            ),
        ],
        "total_candidates": 411,
        "careers_matched": 37,
        "filters_applied": ["region"],
        "candidates_without_each_filter": {"region": 6208},
        "scoring_version": "1.0.0",
        "dataset_source": "Ponte en Carrera (MINEDU)",
        "dataset_snapshot_date": date(2026, 6, 13),
        # Distinta de `dataset_snapshot_date` a proposito: una es la edad de
        # las cifras y la otra la del documento, y un fixture donde coinciden
        # deja pasar el dia en que alguien las cruce.
        "issued_on": date(2026, 8, 11),
    }
    base.update(overrides)
    return OrientationReport(**base)  # type: ignore[arg-type]
