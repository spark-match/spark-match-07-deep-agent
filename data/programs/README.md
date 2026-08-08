# Catálogo real de programas (Ponte en Carrera)

`programs.csv` son 6208 combinaciones de carrera e institución del portal
**Ponte en Carrera** del Ministerio de Educación del Perú: 554 carreras, 1071
universidades e institutos, los 25 departamentos.

Es lo que consulta la herramienta `search_programs` (`src/tools/programs/`), y
es la única fuente de cifras concretas del Perú que tiene el agente. El otro
catálogo, `data/careers/*.md`, describe 20 carreras en general y no sabe nada
de universidades, sueldos ni costos.

## De dónde sale

| | |
|---|---|
| Origen | `spark-match-05-data-pipeline`, `data/features.csv` y `data/riasec_tags.csv` |
| Snapshot del portal | **2026-06-13** (`snapshots/raw_20260613_021109.xlsx`) |
| Generador | `scripts/build_programs_dataset.py` |

Se regenera así, y el resultado es determinista — dos ejecuciones sobre el
mismo origen dan el mismo fichero byte a byte:

```bash
uv run python scripts/build_programs_dataset.py ../spark-match-05-data-pipeline
```

**El dato no se refresca solo.** El portal del MINEDU devuelve HTTP 500 desde
el 2026-07-12 y la etapa `ingest` del `dvc.yaml` del pipeline está congelada,
así que este snapshot es, por ahora, todo lo que hay. Por eso la fecha viaja
en el campo `source` de cada respuesta de la herramienta en vez de decir
«datos oficiales» a secas.

## Lo que hay que saber antes de enseñar una cifra

**La mayoría de los números no son mediciones de ese programa concreto.** El
pipeline rellena lo que el portal no publicó con la mediana de la familia de
carrera. Medido sobre las 6208 filas:

| Campo | Filas estimadas |
|---|---|
| Ingreso mensual | 4528 · **72,9 %** |
| Tasa de admisión | 4048 · **65,2 %** |
| Costo anual | 3096 · **49,9 %** |
| Duración | 1666 · **26,8 %** |

Sólo **370 filas** tienen las cuatro cifras medidas.

Por eso cada fila lleva cuatro banderas `*_measured` y por eso
`search_programs` devuelve en cada resultado la lista `estimated` con los
campos que **no** se midieron. Decirle a un estudiante «en esta universidad
vas a ganar S/ 1442» cuando ese número es la mediana de su familia de carrera
sería inventárselo con formato de dato oficial — que es exactamente el
problema que este catálogo viene a cerrar, no a repetir.

La regla para el modelo está escrita en `src/prompts/coordinator.md` y en
`src/prompts/matching.md`.

## El código RIASEC

La columna `riasec_profile` viene de `riasec_tags.csv` del pipeline. Cubre las
554 carreras, y las 554 están marcadas como `llm_tagged`: **las asignó un
modelo de lenguaje, no el MINEDU**. Sirve para orientar una búsqueda, no como
clasificación oficial.

## Columnas

| Columna | Qué es |
|---|---|
| `source_id` | Id del registro en el portal. Permite volver a la fila de origen. |
| `career`, `career_family` | Nombre de la carrera y su familia. |
| `riasec_profile` | Código de 3 letras. Ver la advertencia de arriba. |
| `institution` | Universidad o instituto que la oferta. |
| `institution_type` | `Universidad` o `Instituto`. |
| `management_type` | `Pública` o `Privada`. |
| `location` | Departamento (25 valores, los mismos que usa el frontend). |
| `duration_years` | Años. |
| `monthly_income` | Ingreso mensual promedio de los egresados, en soles. |
| `annual_cost` | Costo anual, en soles. En públicas suele ser la tasa administrativa, no una matrícula. |
| `admission_rate` | Porcentaje de ingresantes sobre postulantes. |
| `*_measured` | `true` si esa cifra se midió; `false` si es la mediana de la familia. |

Las columnas normalizadas del pipeline (`income_norm` y compañía) se dejan
fuera a propósito: su propio diccionario de datos dice que no deben usarse
para explicarle nada al usuario, porque son transformaciones matemáticas sin
lectura directa.
