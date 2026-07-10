"""Career catalog search tool.

In the MVP this uses a local in-memory catalog.
In production, this will query pgvector via Aurora PostgreSQL.
"""

from langchain_core.tools import tool

# --- In-memory career catalog (MVP) ---
# This will be replaced by a RAG pipeline over pgvector in production.
CAREER_CATALOG: list[dict] = [
    {
        "id": "cs",
        "name": "Ciencias de la Computación",
        "riasec_profile": "IRC",
        "description": "Diseño de algoritmos, desarrollo de software, inteligencia artificial.",
        "skills": ["programación", "lógica", "matemáticas", "resolución de problemas"],
        "field": "Tecnología",
        "outlook": "Muy alta demanda laboral, crecimiento sostenido.",
    },
    {
        "id": "medicine",
        "name": "Medicina",
        "riasec_profile": "ISR",
        "description": "Diagnóstico, tratamiento y prevención de enfermedades humanas.",
        "skills": ["biología", "química", "empatía", "resistencia al estrés"],
        "field": "Salud",
        "outlook": "Demanda estable, especialización requerida.",
    },
    {
        "id": "architecture",
        "name": "Arquitectura",
        "riasec_profile": "AIR",
        "description": "Diseño de espacios habitables, urbanismo, sostenibilidad.",
        "skills": ["dibujo técnico", "creatividad", "física", "visión espacial"],
        "field": "Diseño / Ingeniería",
        "outlook": "Demanda moderada, crecimiento en arquitectura sostenible.",
    },
    {
        "id": "psychology",
        "name": "Psicología",
        "riasec_profile": "SIA",
        "description": "Estudio del comportamiento humano, terapia, investigación.",
        "skills": ["escucha activa", "empatía", "análisis", "comunicación"],
        "field": "Ciencias Sociales",
        "outlook": "Demanda creciente en salud mental.",
    },
    {
        "id": "business_admin",
        "name": "Administración de Empresas",
        "riasec_profile": "ECS",
        "description": "Gestión organizacional, estrategia, finanzas corporativas.",
        "skills": ["liderazgo", "finanzas", "negociación", "planificación"],
        "field": "Negocios",
        "outlook": "Alta demanda, versatilidad laboral.",
    },
    {
        "id": "data_science",
        "name": "Ciencia de Datos",
        "riasec_profile": "ICR",
        "description": "Extracción de insights desde datos masivos, ML, estadística.",
        "skills": ["estadística", "programación", "visualización", "pensamiento crítico"],
        "field": "Tecnología / Ciencia",
        "outlook": "Altísima demanda, uno de los campos de mayor crecimiento.",
    },
    {
        "id": "graphic_design",
        "name": "Diseño Gráfico",
        "riasec_profile": "AER",
        "description": "Comunicación visual, branding, diseño digital y editorial.",
        "skills": ["creatividad", "software de diseño", "tipografía", "color"],
        "field": "Artes / Comunicación",
        "outlook": "Demanda estable, crecimiento en UX/UI.",
    },
    {
        "id": "civil_engineering",
        "name": "Ingeniería Civil",
        "riasec_profile": "RIC",
        "description": "Diseño y construcción de infraestructura: puentes, edificios, carreteras.",
        "skills": ["cálculo", "física", "dibujo técnico", "gestión de proyectos"],
        "field": "Ingeniería",
        "outlook": "Demanda constante, crecimiento en infraestructura sostenible.",
    },
    {
        "id": "marketing",
        "name": "Marketing Digital",
        "riasec_profile": "EAC",
        "description": "Estrategias de comunicación, publicidad, análisis de mercado digital.",
        "skills": ["creatividad", "análisis de datos", "comunicación", "redes sociales"],
        "field": "Negocios / Comunicación",
        "outlook": "Alta demanda, evolución constante con IA.",
    },
    {
        "id": "education",
        "name": "Educación / Pedagogía",
        "riasec_profile": "SAE",
        "description": "Enseñanza, diseño curricular, desarrollo de materiales educativos.",
        "skills": ["comunicación", "paciencia", "creatividad", "organización"],
        "field": "Educación",
        "outlook": "Demanda estable, transformación con tecnología educativa.",
    },
]


@tool
def search_careers(query: str, field: str | None = None) -> list[dict]:
    """Search the career catalog by keyword or field.

    In the MVP, performs simple text matching on the local catalog.
    In production, this will use pgvector semantic search.

    Args:
        query: Search query (career name, skill, or description keywords)
        field: Optional filter by field (e.g., 'Tecnología', 'Salud')
    """
    query_lower = query.lower()
    results = []

    for career in CAREER_CATALOG:
        # Match against name, description, skills, and field
        searchable = " ".join(
            [
                career["name"].lower(),
                career["description"].lower(),
                " ".join(career["skills"]),
                career["field"].lower(),
            ]
        )

        if query_lower in searchable and (
            field is None or field.lower() in career["field"].lower()
        ):
            results.append(career)

    # If no exact match, return all careers in the field (if specified)
    if not results and field:
        results = [c for c in CAREER_CATALOG if field.lower() in c["field"].lower()]

    # If still no results, return top 5 careers as suggestions
    if not results:
        results = CAREER_CATALOG[:5]

    return results
