# Pull Request template — Spark Match Deep Agent

## Resumen

<!-- Breve descripción de los cambios (1-3 líneas) -->

## Tipo de cambio

- [ ] Nueva feature (cambio que añade funcionalidad)
- [ ] Bug fix (cambio que corrige un issue)
- [ ] Refactor (cambio que no añade feature ni arregla bug)
- [ ] Docs (cambios solo en documentación)
- [ ] Tests (añadir o mejorar tests)
- [ ] Chore (cambios operativos: deps, CI, configs)

## Cambios

<!-- Lista detallada de los cambios principales -->

- 
- 
- 

## Testing

<!-- Cómo verificaste los cambios -->

- [ ] Tests unitarios pasan localmente (`uv run pytest`)
- [ ] Lint pasa localmente (`uv run ruff check`)
- [ ] Probado manualmente con el agente en local
- [ ] Probado el endpoint FastAPI

## Checklist

- [ ] Mi PR toca solo archivos que están bajo mi CODE OWNER (ai-devs)
- [ ] He actualizado el README si añadí/modifiqué funcionalidad pública
- [ ] He actualizado CHANGELOG.md si corresponde
- [ ] Mis commits siguen la convención (`feat:`, `fix:`, `chore:`, etc.)
- [ ] He probado que el agente responde correctamente con el cambio

## Notas para reviewers

<!-- Contexto adicional, capturas, links a issues, etc. -->

## Aprobaciones requeridas

Este PR requiere **1 aprobación** de un miembro del team `@spark-match/ai-devs`
(CODE OWNER del repo, ver `.github/CODEOWNERS` y el ruleset
`spark-match-default-branch-protection`). Como el autor no puede aprobar su
propio PR, se necesita un revisor distinto que sea miembro de `ai-devs`.

Si el PR toca un path de gobernanza (`README.md`, `AGENTS.md`,
`ROADMAP-2026-08.md`), `@spark-match/product-owners` es co-owner de esos
archivos específicos y su aprobación también satisface el requisito.
