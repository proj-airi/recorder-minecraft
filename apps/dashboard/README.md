# Dashboard

The dashboard is the browser UI for inspecting and operating recorder-minecraft workflows.

## Development

From the repository root, install dependencies with `pnpm install`, then run `pnpm dev`.
Use `pnpm typecheck`, `pnpm lint`, and `pnpm build` before submitting changes.

Install the Chromium binary used by browser tests once, then run the dashboard suite:

```sh
pnpm --filter @proj-airi/recorder-minecraft-dashboard exec playwright install chromium
pnpm --filter @proj-airi/recorder-minecraft-dashboard test:browser
```

## Scope

Put route-level screens in `src/pages`; `vue-router/vite` generates the route table from
that directory. Keep reusable UI and state outside route components as features grow.

This app is not the place for capture, validation, scene extraction, or rendering logic.
Those responsibilities remain in the existing Go and JVM modules.
