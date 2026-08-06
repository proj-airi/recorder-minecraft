# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`docs/TERMS_AND_CONCEPTS.md`** — this repository's canonical glossary and ubiquitous language.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If `docs/adr/` does not exist, proceed silently. The `/domain-modeling` skill creates it lazily only when a decision meets its ADR criteria.

## Canonical context document

This is a single-context repository. Record every glossary and ubiquitous-language change in `docs/TERMS_AND_CONCEPTS.md`; it replaces the conventional root `CONTEXT.md` for this repository. Do not create `CONTEXT.md` or `CONTEXT-MAP.md` here.

When a term is resolved during domain modeling, update the relevant section of `docs/TERMS_AND_CONCEPTS.md` immediately. Preserve its existing tables and topic-based organization rather than translating it to the generic `CONTEXT.md` format.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `docs/TERMS_AND_CONCEPTS.md`. Don't drift to obsolete terms or conflicting synonyms.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
