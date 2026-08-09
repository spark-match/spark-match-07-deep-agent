"""Student vocational profile schema.

This schema is used by langmem to extract structured profile data
from natural conversations. The agent chats with the student and
langmem progressively fills in the fields as information emerges.
"""

from pydantic import BaseModel, Field


class StudentProfile(BaseModel):
    """Structured vocational profile of a student.

    This model is progressively filled by langmem as the agent
    converses with the student. Fields start as None and are
    populated as the student reveals information naturally.
    """

    # --- Identity ---
    name: str | None = Field(default=None, description="Student's name")
    age: int | None = Field(default=None, description="Student's age")
    education_level: str | None = Field(
        default=None,
        description="Current education level: secundaria, preparatoria, universidad, posgrado",
    )
    current_studies: str | None = Field(
        default=None,
        description="What the student is currently studying, if anything",
    )

    # --- RIASEC Scores (1-10 each) ---
    realistic: int | None = Field(
        default=None,
        description="RIASEC Realistic score (1-10): hands-on, physical, mechanical work",
    )
    investigative: int | None = Field(
        default=None,
        description="RIASEC Investigative score (1-10): analytical, scientific, research",
    )
    artistic: int | None = Field(
        default=None,
        description="RIASEC Artistic score (1-10): creative, expressive, design",
    )
    social: int | None = Field(
        default=None,
        description="RIASEC Social score (1-10): helping, teaching, counseling",
    )
    enterprising: int | None = Field(
        default=None,
        description="RIASEC Enterprising score (1-10): leading, persuading, managing",
    )
    conventional: int | None = Field(
        default=None,
        description="RIASEC Conventional score (1-10): organizing, data, detail-oriented",
    )

    # --- Derived ---
    riasec_code: str | None = Field(
        default=None,
        description="3-letter RIASEC code derived from top 3 scores (e.g., 'IAS', 'RIC')",
    )

    # --- Interests & Context ---
    interests: list[str] = Field(
        default_factory=list,
        description="List of topics, activities, or subjects the student enjoys",
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Self-reported or inferred strengths and skills",
    )
    preferred_fields: list[str] = Field(
        default_factory=list,
        description="Professional fields the student is drawn to",
    )
    dislikes: list[str] = Field(
        default_factory=list,
        description="Activities, subjects, or work environments the student wants to avoid",
    )

    # --- Career Direction ---
    target_career: str | None = Field(
        default=None,
        description="Career the student has chosen or is leaning towards",
    )
    career_goals: str | None = Field(
        default=None,
        description="What the student hopes to achieve professionally",
    )

    # --- Restricciones de busqueda ---
    #
    # Son los cuatro filtros de la pantalla `/filters` del frontend, aqui
    # porque cambian conversando ("uy, eso es carisimo", "prefiero quedarme en
    # Arequipa") y un formulario no puede recogerlos a mitad de charla.
    #
    # Se nombran como las columnas del CSV (`management_type`,
    # `institution_type`) y NO como el frontend, que los llama al reves
    # --su `institutionType` es publica/privada y su `academicType` es
    # universidad/instituto--. Asi la traduccion ocurre en un solo sitio, el
    # borde con el frontend, en vez de propagarse a la memoria del agente.
    #
    # Todos empiezan en None y None significa "no lo sabemos", no "sin filtro".
    # La diferencia importa: `recommend_programs` los aplica como exclusion, y
    # un valor inventado no devuelve una respuesta mala sino que borra opciones
    # en silencio.
    preferred_region: str | None = Field(
        default=None,
        description=(
            "Peruvian department where the student wants to study, e.g. 'Arequipa'. "
            "Only set it when the student states it; never infer it from where they live."
        ),
    )
    preferred_management: str | None = Field(
        default=None,
        description=(
            "Whether the student wants a public or private institution: "
            "'pública' or 'privada'. Leave None if they have no preference or "
            "have not said."
        ),
    )
    preferred_institution_type: str | None = Field(
        default=None,
        description=(
            "Whether the student wants a university or an institute: "
            "'universidad' or 'instituto'. Leave None if they have no "
            "preference or have not said."
        ),
    )
    max_annual_budget: float | None = Field(
        default=None,
        description=(
            "Maximum annual tuition the student can afford, in Peruvian soles. "
            "Only set it when the student gives an actual figure. Do NOT turn "
            "vague statements like 'no tengo mucho dinero' into a number: leave "
            "it None and let the agent ask."
        ),
    )

    @property
    def has_riasec_profile(self) -> bool:
        """Check if enough RIASEC data exists to compute a profile."""
        scores = [
            self.realistic,
            self.investigative,
            self.artistic,
            self.social,
            self.enterprising,
            self.conventional,
        ]
        return all(s is not None for s in scores)

    @property
    def profile_completeness(self) -> float:
        """Calculate how complete the profile is (0.0 to 1.0).

        Las cuatro restricciones de busqueda (`preferred_region`,
        `preferred_management`, `preferred_institution_type`,
        `max_annual_budget`) NO cuentan aqui, a proposito. Esta medida es lo
        bien que conocemos al estudiante en lo VOCACIONAL, y es lo que decide
        si se le puede emitir un informe (ADR-019, D8). Las restricciones son
        preferencias opcionales: quien no ha dicho presupuesto merece su
        informe igual. Meterlas en el denominador convertiria un filtro
        opcional en un bloqueo, y ademas bajaria de golpe la completitud de
        todos los perfiles ya guardados.
        """
        fields = [
            self.name,
            self.age,
            self.education_level,
            self.realistic,
            self.investigative,
            self.artistic,
            self.social,
            self.enterprising,
            self.conventional,
        ]
        filled = sum(1 for f in fields if f is not None)
        filled += min(len(self.interests), 3)  # Up to 3 interests count
        total = len(fields) + 3
        return round(filled / total, 2)
