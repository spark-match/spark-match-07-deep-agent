# Eventos propios sobre AG-UI

Contrato entre este repositorio y `spark-match-04-frontend`. Cubre solo lo
que anadimos nosotros; el resto de eventos (`TEXT_MESSAGE_*`, `TOOL_CALL_*`,
`STEP_*`, `MESSAGES_SNAPSHOT`, ...) son los del protocolo AG-UI y los emite
`ag-ui-langgraph`, no este codigo.

## Por que hacen falta

`deepagents` no expone la delegacion en subagentes como nada observable
desde fuera. Los tres especialistas (`assessment`, `matching`, `planning`)
viven detras de **una sola** herramienta llamada `task`, y su handler corre
el subagente entero con `ainvoke()` dentro de esa misma llamada. Al
navegador solo le llega `toolCallName="task"`: no se puede decir a cual se
delego, ni cuando termino, ni si fallo.

AG-UI tampoco define un evento de subagente. Lo que si define
`ag-ui-langgraph` es un pasillo generico: cualquier `on_custom_event` de
LangGraph cuyo nombre no sea uno de los cuatro reservados
(`manually_emit_message`, `manually_emit_tool_call`, `manually_emit_state`,
`exit`) se traduce a un evento AG-UI de tipo `CUSTOM` y viaja por el mismo
stream SSE. Es el camino que usa `src/agent/subagent_events.py`.

## Los dos eventos

Ambos llegan como `{"type": "CUSTOM", "name": ..., "value": {...}}`.

### `spark.subagent.start`

Se emite justo antes de que el subagente empiece a trabajar.

| Campo | Tipo | Que es |
|---|---|---|
| `toolCallId` | `string` | El `id` de la llamada a `task`. Es la clave que empareja este evento con su `TOOL_CALL_START` y con su cierre. |
| `subagent` | `string` | `assessment`, `matching`, `planning`, o `desconocido` si el modelo llamo a `task` sin decir a quien. |

### `spark.subagent.end`

Se emite cuando el subagente termina, **tambien si revienta**. Sin eso, un
fallo dejaria el indicador girando para siempre en la pantalla.

| Campo | Tipo | Que es |
|---|---|---|
| `toolCallId` | `string` | El mismo del `start`. |
| `subagent` | `string` | El mismo del `start`. |
| `ok` | `boolean` | `false` cuando la delegacion lanzo una excepcion. |
| `durationMs` | `number` | Reloj monotono, medido desde fuera de toda la cadena de middlewares de herramienta. |

## Reglas para quien consume

1. **`subagent` es una clave, no un texto que ensenar.** La copia que lee el
   estudiante la pone el frontend. Aqui viaja un identificador estable, y el
   que no conozca se pinta con una etiqueta generica, igual que ya se hace
   con `toolCallName`.
2. **Un evento desconocido se ignora, no rompe.** Agregar un evento nuevo del
   lado del agente nunca puede tumbar al frontend.
3. **`description` no viaja.** Es el otro argumento de `task`: la instruccion
   que el coordinador redacta para el subagente, o sea un prompt interno.
   Mismo criterio por el que `src/api/app.py` filtra los eventos `RAW` antes
   de que salgan del servidor.
4. **El `start` puede llegar sin su `end` si el proceso muere a mitad.** El
   cierre del turno (`RUN_FINISHED` o `RUN_ERROR`) es el limite: a partir de
   ahi, nada puede seguir corriendo.
