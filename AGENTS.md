# Repository Guidelines

## Project Structure & Module Organization

- `cmd/recorder-minecraft/` and `internal/` contain the Go provisioning, validation, export, scene, and render-job CLI. Go tests live beside the affected packages.
- `apis/proto/` defines artifact contracts; generated Go types live under `apis/sdk/go/`. `databases/scene/` models each play-local Scene Store V2 with Ent.
- `mods/recorder-mod/` is the Kotlin/Java Fabric server mod; production code is under `src/main/` and JUnit tests under `src/test/`.
- `processors/scene-extractor/` is the headless Java CLI that uses Minecraft, Fabric, and Arcade Replay APIs to build random-access block/entity scene stores. `mods/renderer-mod/` is the client-only first-person RGB renderer.
- `docs/specs/` defines the source JSONL and dataset contracts. Update these documents when changing persisted fields or invariants.
- `deploy/` contains Docker Compose configuration. `ServerReplay/` is upstream source; avoid unrelated edits there.
- `artifacts/`, `.recorder/minecraft/`, `runtime/`, and renderer `run/` directories are generated and must not be committed.

## Build, Test, and Development Commands

Install the pinned Go, Buf, and JVM toolchains:

```sh
proto install --config-mode local
```

Choose checks from the files changed:

- Go changes: run `gofmt` on changed files, then `go test ./...` and
  `golangci-lint run ./...`.
- `apis/proto/` changes: run `buf format -w`, `buf lint`, and `buf generate`;
  inspect changes under `apis/sdk/go` and `apis/sdk/jvm`, then test every Go or
  JVM consumer affected by the contract.
- `mods/recorder-mod/` changes: run
  `gradle --project-dir mods/recorder-mod build`.
- `processors/scene-extractor/` changes: run
  `gradle --project-dir processors/scene-extractor build installDist`.
- `mods/renderer-mod/` changes: run
  `gradle --project-dir mods/renderer-mod build`.
- Server image changes: run
  `gradle --project-dir mods/recorder-mod stageServerImage`; when the extractor
  or Docker build context changes, also build the extractor distribution and
  run the `docker buildx build` command documented in `README.md`.
- CI, toolchain, generated-code, or cross-module changes: run the complete
  pre-submit command sequence in `README.md`.

Use `MC_RECORDER_FLASHBACK_JAR=/path/to/Flashback-0.39.5.jar` for renderer builds when Modrinth is unavailable. For an end-to-end capture, run `./hack/minecraft-server start`, join `localhost:25565`, stop it with `./hack/minecraft-server stop`, and exercise processors against the completed play without mutating its capture inputs.

## Coding Style & Naming Conventions

Use `gofmt` for Go. Follow standard Go naming, Java/Kotlin `UpperCamelCase` types, and `lowerCamelCase` members. Keep CLI errors actionable. Prefer small functions around integrity boundaries; never weaken containment, SHA-256, completion, or provenance checks. Match adjacent Java/Kotlin code and keep `git diff --check` clean.

## Testing Guidelines

Go tests use the standard `testing` package and files named `*_test.go`. JVM module tests use JUnit 5/Kotlin Test and classes named `*Test`. Add regression coverage beside the affected module. Changes to capture alignment, rendering, or retention should also be exercised with a completed real replay; do not mutate source captures during verification.

## Readability, Naming, and Comments

- Prefer names that rely on the module boundary for context instead of repeating package, product, protocol, or transport prefixes inside every symbol. A well-named module should let exported functions use short action-first names; repeat the larger context only when the symbol crosses a boundary where that context is no longer obvious.
- Name functions after the domain operation they perform, not after the implementation layer that happens to contain them. This keeps call sites readable after refactors and avoids names becoming stale when code moves between files.
- Avoid names that encode multiple layers of ownership into one symbol. If a name needs several qualifiers to be understandable, reconsider the module boundary or introduce a clearer local concept.
- Use nouns for resolved domain concepts and verbs for transformations or side effects. When a function derives a policy/configuration from an event or request, name the domain result explicitly so callers understand what decision is being made.
- Comments should reduce reader uncertainty, not increase documentation volume.
- Write comments where a reader would otherwise ask why this case can happen, why this branch is ignored, why this fallback exists, why this order matters, what state changed here, what external side effect just happened, or what protocol/invariant this line is preserving.
- Good comments explain hidden intent, constraints, ownership, invariants, ordering, side effects, protocol shape, or non-obvious fallback behavior.
- Bad comments translate code into English, restate names/types, or exist only to satisfy hover documentation.
- Important implementation comments should live near the confusing line or branch, not only on exported declarations.
- For calculation-heavy code, prefer inline comments near the intermediate values and branches that need explanation. Do not rely only on function-level JSDoc when the hard part is a coordinate system, unit conversion, clamp, rounding rule, aggregation, fallback, or precedence decision.
- Apply this especially to geometry, graphics and shader math, billing or metering, analytics or statistics, UI layout and positioning, ranking or scoring, and normalization code.
- Format longer comments as short paragraphs separated by blank comment lines. Do not compress background, symptom, rejected alternatives, final rationale, and references into one dense block.
- For investigation-heavy comments, prefer this order when useful: source/context, observed failure, why the obvious fix is insufficient, chosen fix, and references/removal condition.
- Do not add broad comments like `// Config`, `// Host`, or `// Update state` unless they explain a non-obvious boundary or transition.
- Add clear, concise comments for utils, math, OS-interaction, algorithm, shared, and architectural functions that explain non-obvious intent, invariants, constraints, or why the code is needed.
- When using a workaround, add a `// NOTICE:` comment explaining why, the root cause, and any source context. If validated via `node_modules` inspection or external sources (e.g., GitHub), include relevant line references and links in code-formatted text.
- When copying, adapting, or deriving code, configuration, constants, schemas, ignore patterns, algorithms, or rules from an external repository, add a nearby `// NOTICE:` comment with a GitHub permalink pinned to the source commit and exact line reference. Use single-line references like `https://github.com/eslint/eslint/blob/833ec10fd702644e94334edd3cd2aa313174a958/.editorconfig#L11` and multi-line references like `https://github.com/eslint/eslint/blob/833ec10fd702644e94334edd3cd2aa313174a958/.editorconfig#L11-L17`. Do not use branch links such as `main` or links without line numbers for traceable source references.
- When moving/refactoring/fixing/updating code, keep still-accurate comments with the code. Remove obsolete comments rather than preserving their history in source; explain notable removals in review notes when needed.
- Avoid stubby/hacky scaffolding; prefer small refactors that leave code cleaner.
- Use markers:
  - `// TODO:` follow-ups
  - `// REVIEW:` concerns/needs another eye
  - `// NOTICE:` magic numbers, hacks, important context, external references/links

## Commit & worktree management Workflow Tips

- Do not commit any spec or plan.
- Do not commit without proper testing and validation.
- Do not commit once you just edited something, you should properly organize and group related changes into a single commit.
- Prefer to not to commit for easier for user to review, if committed, only Pull Request can review, without types.

## PR / Workflow Tips

- Rebase pulls; branch naming `username/feat/short-name`; clear commit messages (gitmoji is prohibited).
- Summarize changes, how tested (commands), and follow-ups.
- Improve legacy you touch; avoid one-off patterns.
- Keep changes scoped; use workspace filters (`pnpm -F <package> <script>`).
- Use Conventional Commits for commit messages (e.g., `feat(<package name>): added something`).

## Agent skills

### Issue tracker

Issues are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

The canonical triage roles use their default GitHub label names. See `docs/agents/triage-labels.md`.

### Domain docs

Use `docs/TERMS_AND_CONCEPTS.md` as the canonical ubiquitous language. See `docs/agents/domain.md` before naming or changing domain concepts.
