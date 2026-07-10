# Career catalog — `data/careers/`

Each career is a single Markdown file with **YAML frontmatter** containing the
structured fields and a Markdown body with descriptive content. Adding a new
career = creating one `.md` file here. No Python code changes required.

## Frontmatter schema

| Field            | Type   | Required | Notes                                                  |
|------------------|--------|----------|--------------------------------------------------------|
| `id`             | str    | yes      | URL-safe slug, unique across the catalog.              |
| `name`           | str    | yes      | Display name in Spanish.                               |
| `riasec_profile` | str    | yes      | 3-letter RIASEC code (e.g. `IRC`, `SIA`).              |
| `field`          | str    | yes      | Career field, used for filtering (case-insensitive).   |
| `outlook`        | str    | yes      | Market / outlook summary.                              |

## Body conventions

The Markdown body is optional but encouraged. Suggested sections:

- `## Descripción` — 1-2 sentences.
- `## Habilidades clave` — bulleted list.
- `## Recursos para empezar` — free courses, books, YouTube channels.

## Adding a new career

1. Create `data/careers/{your-id}.md`.
2. Fill in the frontmatter (all required fields).
3. Add a body with description and resources.
4. Open a PR. The catalog is loaded automatically on agent startup.

## Why Markdown?

- **No code review needed** for product / content contributors.
- **Git diffs are human-readable** — see exactly what changed.
- **Frontmatter gives structure** for the agent's matching engine.
- **Body gives richness** for the planning subagent's resource recommendations.
- Aligns with how `00-knowledge-base` already stores ADRs and SDDs.