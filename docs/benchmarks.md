# Benchmarks — Spark Match Deep Agent

> **Sprint 9, task 9.B.4**: registrar P50/P90/costo/helpfulness para
> comparar el Deep Agent actual contra los POC v1 (custom loop, AWS
> Bedrock) y POC v2 (AWS Bedrock AgentCore Harness). El objetivo NO es
> "vencer" a los POC sino dejar una linea base honesta contra la cual
> medir cada cambio futuro.

---

## 1. Fuentes de verdad

| Fuente | Datos | Estado al 2026-08-04 |
|---|---|---|
| `D:\Continital\orion\spark-match-poc-v1\` (no presente en este workspace; cifras citadas desde `poc-v2-decision.md` §3.1) | POC v1 custom loop sobre Bedrock | Cifras historicas |
| `D:\Continital\orion\spark-match-poc-v2\docs\AWS-HARNESS-POC-V*.md` | POC v2 Harness (V3-V9), AgentCore | Medido en AWS, citado verbatim |
| Este repo, `evals/runner.py --mode live` | Deep Agent (actual) | **Pendiente -- Sprint 11** |

Cifras tomadas textualmente de los documentos referenciados; este archivo
no agrega numeros propios para Deep Agents hasta que se pueda medir en
un entorno con AWS Bedrock disponible. Cualquier cifra Deep Agent
"actual" en este documento es **prospectiva** (objetivo a alcanzar), no
**medida**.

---

## 2. Metricas y como se miden

| Metrica | Definicion | Donde se mide |
|---|---|---|
| **P50 latency** | Mediana de tiempo por turno, end-to-end (request -> ultima AIMessage) | `evals/runner.py --mode live` envuelve cada caso con `time.perf_counter()` |
| **P90 latency** | Percentil 90 de la misma distribucion | Igual |
| **Costo por turno** | USD gastado en invocaciones a Bedrock (input + output tokens) durante un turno | Bedrock Converse API response incluye `Usage` con tokens; precio por modelo desde `aws pricing` |
| **Helpfulness** | Score 1-5 de LLM-as-judge (Haiku 4.5) sobre la respuesta del agente | `evals.judge.SparkMatchJudge` con rubrica multi-dimension (Sprint 9, 9.B.2) |

Los mocks (`--mode mock`) no producen cifras de latencia ni costo
relevantes porque NO invocan Bedrock; producen "scores" heuristicos que
**no son comparables** con Helpfulness del POC v2.

---

## 3. Tabla comparativa

### 3.1 Latencia (segundos / turno)

| Implementacion | P50 | P90 | Costo | Helpfulness | Condiciones |
|---|---|---|---|---|---|
| POC v1 (custom loop) | 8 | ~25 (estimado) | $0.005 | 3.8/5 | 3-5 tool calls/turn, sin router |
| POC v2 V3 (Harness, baseline) | 13.0 | 45.0 | $0.010 | 4.24/5 | 6.5 tool calls/turn secuenciales |
| POC v2 V6 (quick wins) | 11.0 | 31.7 | $0.0075 | -- | Batch tool calls, cache lookups |
| POC v2 V8 (router agresivo) | **8.5** | **25.5** | $0.0056 | 4.35/5 | 38.5% Haiku coverage |
| POC v2 V9 (V8 + quality) | 8.5 | 25.5 | $0.0056 | **4.42/5** | Validado como production-ready |
| **Deep Agent (este repo)** | **Pendiente -- Sprint 11** | Pendiente | Pendiente | Pendiente | Router (Sprint 8, 8.4) deberia replicar V8 |

> **Sources**:
> POC v1: `spark-match-poc-v2/docs/poc-v2-decision.md` SS3.1, SS3.2
> (cited verbatim); POC v2 V3/V6/V8/V9: `AWS-HARNESS-POC-V{3,6,8,9}.md`
> tablas de latency.

### 3.2 Calidad (LLM-as-judge Haiku 4.5, escala 1-5)

| Implementacion | Relevance | Helpfulness | Language match (/3) | Tone (/3) | Notas |
|---|---|---|---|---|---|
| POC v1 (custom loop) | -- | 3.8 | -- | -- | Sin rubrica formal en POC v1 |
| POC v2 V3 (Harness baseline) | 4.23 | 4.24 | 2.49 | 2.99 | Pre-LANGUAGE-RULE |
| POC v2 V4 (post-language rule) | -- | 4.24+ | **2.92** | **2.99** | Lesson 5 POC v2: +0.46 language match |
| POC v2 V9 (V8 + quality) | -- | **4.42** | -- | -- | V9 cerro el sprint de latency |
| **Deep Agent (este repo)** | Pendiente | Pendiente | Pendiente | Pendiente | LANGUAGE RULE ya aplicada (Sprint 9, 9.A.5) |

> **Sources**: `AWS-HARNESS-POC-V4.md` SS1, `AWS-HARNESS-POC-V9.md`
> SS2, `poc-v2-decision.md` SS3.

---

## 4. Estimaciones Deep Agent (prospectivas, no medidas)

Estas cifras son **objetivos a alcanzar**, no mediciones. El DoD del
Sprint 9 no requiere medirlas (requiere AWS Bedrock -- AGENTS.md hard
rule #7: el agente debe correr local sin cuenta AWS), asi que se
proponen como targets a validar en Sprint 11 cuando se despliegue el
agente en `dev` y se pueda correr `--mode live`.

| Metrica | Target | Razon |
|---|---|---|
| P50 latency | **<= 8.5s** | Emular POC v2 V8 (V9 production-ready) |
| P90 latency | **<= 25.5s** | Emular POC v2 V8 |
| Costo / turno | **<= $0.0056** | Emular POC v2 V8 con router 38% Haiku |
| Helpfulness | **>= 4.42/5** | Emular POC v2 V9 |
| LANGUAGE match | **>= 2.92/3** | Ya integrada via Sprint 9, 9.A.5 (defense-in-depth en 4 prompts + skill) |

El router Haiku/Sonnet ya esta activo (Sprint 8, 8.4) con heuristica
pura, **sin** llamada extra a LLM para clasificar -- el `-26% latency /
-44% cost` que midio el POC v2 deberia replicarse aqui, pero hay que
medirlo con Bedrock para confirmarlo.

---

## 5. Que se puede medir HOY (sin AWS)

Tres mediciones que SÍ son posibles hoy en CI (mock mode, sin
Bedrock), utiles para detectar regresiones de rendimiento **dentro del
proyecto** aunque no comparables con el POC v2:

### 5.1 Latencia del handler puro

```bash
uv run python -m pytest tests/tools/ --tb=short --durations=10
```

Reporta las 10 tests mas lentas de la suite de tools. Un handler
puro (`evaluate_riasec_profile_handler`, `calculate_affinity_handler`)
tarda <5ms en local; un handler async con I/O deberia estar en <50ms.
**Regression guard**: si `evaluate_riasec_profile` supera 50ms, hay un
bug.

### 5.2 Throughput del grafo en mock mode

```bash
uv run python -c "
import time
from evals.runner import run_eval
start = time.perf_counter()
results = run_eval(mode='mock')
elapsed = time.perf_counter() - start
print(f'{len(results)} cases in {elapsed:.2f}s = {elapsed/len(results)*1000:.0f}ms/case')
"
```

**Benchmark actual (medido 2026-08-04 en este repo, dev branch)**:
30 cases in **~0.5s** = **~17ms/case** (mock mode, sin LLM, sin
Bedrock). Es 100x+ mas rapido que live mode porque no hay I/O.

### 5.3 Tamano del bundle

```bash
uv run python -c "
from importlib.metadata import distributions
total = sum(d.size or 0 for d in distributions()) / 1024 / 1024
print(f'total installed: {total:.1f} MB')
"
```

Util para detectar dependencias infladas.

---

## 6. Plan para Sprint 11 (cuando haya AWS deploy)

1. Desplegar el agente en `dev` (Sprint 10.A).
2. Activar el modulo de observabilidad (Sprint 11.A) que captura
   latencia por turno, tokens por llamada, costo por Bedrock response.
3. Correr `--mode live` con los 30 casos del dataset
   (`evals/dataset.jsonl`) y agregar las 3 sub-tablas de SS3.1 con
   cifras Deep Agent **medidas**.
4. Comparar contra los targets de SS4 y publicar resultado en este
   archivo.
5. Si P50 > 8.5s o P90 > 25.5s: investigar regresiones antes de
   mergear (Sprint 11.5 fue el destino original para esto, pero no
   esperamos llegar ahi -- los targets son aspiracionales hasta que se
   mida).

---

## 7. Cosa que NO son benchmarks

- **Evals `--mode mock` pass-rate**: util como regression guard pero
  NO es un benchmark comparable. Solo verifica que el mock runner
  detecta outputs vacios y fingerprints de tools; no mide Helpfulness.
- **Tests unitarios con `durations`**: util como regression guard de
  latency de handlers puros; NO comparable con latencia de POC v2.
- **Cobertura de lineas**: util como gate de calidad pero NO es un
  indicador de Helpfulness.

Cualquier otra fuente de cifras Deep Agent que no venga de
`--mode live` + observabilidad (Sprint 11) **no debe** citarse como
benchmark.