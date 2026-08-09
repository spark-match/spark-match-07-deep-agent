"""Tests for the domain models."""

from src.models.profile import StudentProfile


class TestStudentProfile:
    """Tests for the StudentProfile schema."""

    def test_empty_profile(self):
        profile = StudentProfile()
        assert profile.name is None
        assert profile.has_riasec_profile is False
        assert profile.profile_completeness == 0.0

    def test_partial_profile(self):
        profile = StudentProfile(
            name="Alice",
            realistic=3,
            investigative=9,
            interests=["programación", "ciencia"],
        )
        assert profile.name == "Alice"
        assert profile.has_riasec_profile is False  # Missing 4 scores
        assert profile.profile_completeness > 0.0

    def test_complete_riasec(self):
        profile = StudentProfile(
            name="Bob",
            age=17,
            education_level="preparatoria",
            realistic=3,
            investigative=9,
            artistic=7,
            social=5,
            enterprising=4,
            conventional=2,
            interests=["math", "physics", "coding"],
        )
        assert profile.has_riasec_profile is True
        assert profile.profile_completeness == 1.0

    def test_profile_serialization_incluye_las_restricciones(self):
        perfil = StudentProfile(
            preferred_region="Arequipa",
            preferred_management="pública",
            preferred_institution_type="universidad",
            max_annual_budget=8000.0,
        )
        data = perfil.model_dump()

        assert data["preferred_region"] == "Arequipa"
        assert data["max_annual_budget"] == 8000.0
        assert StudentProfile.model_validate(data) == perfil

    def test_profile_serialization(self):
        profile = StudentProfile(
            name="Carlos",
            realistic=5,
            investigative=8,
            artistic=6,
            social=4,
            enterprising=3,
            conventional=7,
            riasec_code="ICR",
            interests=["datos", "estadística"],
        )
        data = profile.model_dump()
        assert data["name"] == "Carlos"
        assert data["riasec_code"] == "ICR"
        assert "datos" in data["interests"]

        # Round-trip
        restored = StudentProfile.model_validate(data)
        assert restored == profile


class TestRestriccionesDeBusqueda:
    """Los cuatro filtros de `/filters`, ahora parte del perfil.

    Viven aquí porque cambian conversando —«uy, eso es carísimo», «prefiero
    quedarme en Arequipa»— y un formulario no puede recogerlos a mitad de
    charla. Ver ADR-019 y `EXTRACTION_INSTRUCTIONS`.
    """

    def test_nacen_todas_vacias(self):
        """None significa «no lo sabemos», no «sin filtro».

        La diferencia importa: `recommend_programs` las aplica como exclusión,
        y un valor inventado no da una respuesta mala, borra opciones en
        silencio. Un default distinto de None sería una restricción que el
        estudiante nunca pidió —justo lo que hace hoy el `budget: 8000` de
        `DEFAULT_FILTERS` en el frontend.
        """
        perfil = StudentProfile()

        assert perfil.preferred_region is None
        assert perfil.preferred_management is None
        assert perfil.preferred_institution_type is None
        assert perfil.max_annual_budget is None

    def test_no_cuentan_para_la_completitud(self):
        """Deliberado: la completitud mide lo VOCACIONAL, y es lo que decide si
        se puede emitir un informe (ADR-019, D8). Quien no ha dicho presupuesto
        merece su informe igual; meterlas en el denominador convertiría un
        filtro opcional en un bloqueo y bajaría de golpe la completitud de todos
        los perfiles ya guardados."""
        vocacional = {
            "name": "Bob",
            "age": 17,
            "education_level": "secundaria",
            "realistic": 3,
            "investigative": 9,
            "artistic": 7,
            "social": 5,
            "enterprising": 4,
            "conventional": 2,
            "interests": ["math", "physics", "coding"],
        }
        sin_restricciones = StudentProfile(**vocacional)
        con_restricciones = StudentProfile(
            **vocacional,
            preferred_region="Arequipa",
            preferred_management="pública",
            preferred_institution_type="universidad",
            max_annual_budget=8000.0,
        )

        assert sin_restricciones.profile_completeness == 1.0
        assert con_restricciones.profile_completeness == 1.0

    def test_el_vocabulario_es_el_del_csv_no_el_del_frontend(self):
        """`preferred_management` es pública/privada y `preferred_institution_type`
        universidad/instituto, igual que las columnas del dataset.

        El frontend los llama al revés —su `institutionType` es pública/privada
        y su `academicType` universidad/instituto—, así que la traducción va en
        el borde con el frontend y no se propaga a la memoria del agente.
        """
        perfil = StudentProfile(
            preferred_management="privada",
            preferred_institution_type="instituto",
        )

        assert perfil.preferred_management == "privada"
        assert perfil.preferred_institution_type == "instituto"
