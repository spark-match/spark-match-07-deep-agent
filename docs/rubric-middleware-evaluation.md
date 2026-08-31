# RubricMiddleware evaluation — Sprint 9, task 9.B.5

> Roadmap 9.B.5: "``deepagents`` expone ``RubricMiddleware`` -- evaluar
> si sustituye parte del judge propio". Conclusion: **no sustituye,
> complementa**. Detalle y razonamiento abajo.

## 1. TL;DR

| Aspecto | `RubricMiddleware` (deepagents 0.6.12) | Nuestro `SparkMatchJudge` |
|---|---|---|
| Cuando corre | In-loop (`before_agent` / `after_agent`) | Post-loop (despues de `ainvoke`) |
| Que hace | Itera hasta que el grader diga "satisfied" | Puntua la respuesta final 0-1 |
| Modelo del grader | Configurable; default = mismo que el agente | Hardcoded a Haiku 4.5 |
| Output del grader | `RubricEvaluation` (criteria + explanation + status) | `JudgeScore` (4 dims + score ponderado + passed) |
| Funcion | **Mejora la respuesta** (in-loop) | **Mide la respuesta** (post-loop) |

Conclusión: son **complementarios**, no sustitutos. `RubricMiddleware`
mejora la respuesta antes de que salga; `SparkMatchJudge` la mide
despues para CI / evals / observabilidad.

## 2. Por que no sustituye

`SparkMatchJudge` resuelve un problema distinto al que resuelve
`RubricMiddleware`. Mezclarlos seria conceptual y operacionalmente
incorrecto:

- **`RubricMiddleware` es in-loop**: cuando el agente termina su turno,
  el middleware llama a un *grader sub-agent* que evalua la respuesta
  contra una rúbrica caller-supplied. Si no esta "satisfied", el agente
  itera otra vez. Esto **cuesta** un LLM call extra por turno
  (potencialmente varios en el peor caso si `max_iterations` no se
  satisface).

- **`SparkMatchJudge` es post-loop**: corre DESPUES de `ainvoke`,
  puntuando la respuesta final. **No cuesta** nada durante la
  invocacion del usuario (solo cuando se corren evals). Mide el output
  contra `expected_*` del dataset con umbrales numericos reproducibles
  en CI (PASSING_SCORE = 0.7).

Sustituir `SparkMatchJudge` por `RubricMiddleware`:

1. **Eliminaria las dimensiones numericas** del rubric actual
   (riasec_accuracy 0.4, career_relevance 0.3, tone 0.2, safety 0.1).
   `RubricMiddleware` solo sabe si "satisfied" o no -- un bool por
   criterio, no un score 0-1.
2. **Eliminaria el pass-rate** del CI: no hay forma de calcular
   "passed at weighted >= 0.7" sin las dimensiones numericas.
3. **Eliminaria la calibracion del POC v2**: el umbral 0.7 fue
   tuneado contra 200 conversaciones en el POC v2 -- no hay
   equivalente en `RubricMiddleware`.
4. **Cambiaria el contrato**: `SparkMatchJudge` mide el output
   INDEPENDIENTE del agente (post-hoc, replicable); `RubricMiddleware`
   MEJORA el output (in-loop, no reproducible sin rerun).

## 3. Por que complementa

`RubricMiddleware` podria **complementar** `SparkMatchJudge` anadiendose
al stack del agente para subir la calidad de las respuestas ANTES de
que el judge las mida. Hipotesis: una respuesta iterada por el grader
sub-agent deberia puntuar MAS ALTO en el judge, porque el grader ya
detecto y corrigio las partes debiles.

Pero esta hipotesis no esta validada empiricamente. Para validarla
habria que:

1. Desplegar el agente (Sprint 10/11) con y sin `RubricMiddleware`.
2. Correr `--mode live` sobre los 30 casos del dataset
   (ahora posible, ver PR #48 que amplio el dataset).
3. Comparar `SparkMatchJudge.value` antes y despues.

Esto pertenece al Sprint 11 (observabilidad + live mode), NO al Sprint
9. Documentado en `docs/benchmarks.md` SS6 plan.

## 4. Implementacion: middleware cableado opcional

`src/agent/factory.py` ahora acepta `enable_rubric: bool = False` (ver
PR que acompana este doc). Cuando es True, `RubricMiddleware` se
anade al stack DESPUES de `MaxTurnsMiddleware` (orden importa: el
grader deberia correr con el presupuesto de tokens ya controlado).

El middleware es **no-op sin un `rubric` en el state de invocacion**,
asi que añadirlo al stack por defecto seria gratis -- pero el costo de
tenerlo activo *permanentemente* (modelo grader instanciado, gc
pressure, etc.) no es cero. Por defecto esta DESACTIVADO y se activa
por `create_spark_agent(enable_rubric=True)` solo cuando el caller lo
necesita.

## 5. API expuesta

`RubricMiddleware` es `.. beta::` en deepagents 0.6.12 -- API
inestable, puede cambiar entre versiones. Cableado opcional significa
que un upgrade de deepagents que rompa la API no rompe el flujo
principal del agente (solo rompe `enable_rubric=True`).

Si el caller quiere self-evaluation hoy, el patron documentado es:

```python
agent = create_spark_agent(enable_rubric=True)
result = await agent.ainvoke(
    {
        "messages": [HumanMessage(content="Quiero explorar carreras")],
        "rubric": (
            "Respuesta satisfactoria = (1) refleja al menos 2 aficiones "
            "del usuario; (2) propone 3 o mas carreras afines; (3) "
            "incluye siguiente paso concreto."
        ),
    },
    config={"configurable": {"thread_id": "..."}},
)
# `_rubric_status`, `_rubric_iterations`, `_rubric_evaluations` quedan
# en agent.get_state(config).values (atributos private, fuera del
# I/O schema publico).
```

## 6. Conclusion formal

| Pregunta del roadmap | Respuesta |
|---|---|
| Sustituye parte del judge propio? | **No** |
| Complementa? | **Si, en produccion** (subir calidad antes de medir) |
| Se debe cablear por defecto? | **No** (costo in-loop, API beta, requiere validacion empirica) |
| Se debe documentar? | **Si** (este archivo) |
| El caller debe poder activarlo? | **Si** (`enable_rubric=True` en `create_spark_agent`) |

Sprint 9, task 9.B.5 cerrado con esta evaluacion + el cableado
opcional + tests del comportamiento no-op. La validacion empirica
queda para Sprint 11 cuando haya live mode + observabilidad.