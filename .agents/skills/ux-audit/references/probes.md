# Probes

How to get L2 and L3 evidence for this dashboard.

## Run it on real data

```sh
go run ./cmd/recorder-minecraft serve   # artifact API at http://127.0.0.1:8080
pnpm dev                                # dashboard; Vite proxies /api, /assets, /item-icons, /game-textures to the API
```

`serve` reads `recorder.toml` (or `-c <path>`); its `paths.artifacts` must hold completed Plays. Point the dashboard at another API with `RECORDER_MINECRAFT_API_URL`. Drive the browser with the `agent-browser` skill, always in a named session (`AGENT_BROWSER_SESSION`).

Before measuring, add a world session to the timeline so the panels are populated, and use a fixed viewport (for example 1600×1000). Record the viewport and the data (sessions, Plays) in the report.

Setup traps, each of which cost a run:

- **Ports.** 8080 and 5173 are often taken by another session. Use `serve --http-address 127.0.0.1:<port> --grpc-address 127.0.0.1:<port>` and start Vite with `RECORDER_MINECRAFT_API_URL` and `--port <port> --strictPort`. Only stop processes you started.
- **Artifacts are copied, not linked.** The catalog refuses symlinked artifact roots and files (containment). Build an audit root by cloning with `cp -cR` (APFS clone, no extra space) and point a scratch `recorder.toml` at it. Never edit the source capture; force failures in the copy.
- **First load reloads.** On a cold cache Vite optimizes dependencies and reloads the page once; wait and reload before driving.
- **HTTP cache hides deleted files.** After removing a file from the copy and rescanning (`POST /api/v1/catalog:refresh`), the browser can still serve the old file as 200. Close the agent-browser session (fresh profile) before checking the missing state.
- **Item and HUD icons** need `[assets] minecraft_client_jar` in the scratch config (for example `~/.gradle/caches/fabric-loom/1.21.8/minecraft-client.jar`); without it icons fall back to text, which changes the render.

## Hierarchy probe (`no-flat-hierarchy`)

Run in the page. It reports the share of visible text per font size and weight, and each Dockview group's share of the viewport.

```js
(() => {
  const W = innerWidth, H = innerHeight, sizes = {}
  let total = 0
  for (const el of document.querySelectorAll('body *')) {
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('')
    if (!text) continue
    const r = el.getBoundingClientRect()
    if (!r.width || r.bottom < 0 || r.top > H || r.right < 0 || r.left > W) continue
    const cs = getComputedStyle(el)
    if (cs.visibility === 'hidden' || cs.opacity === '0') continue
    const key = `${cs.fontSize} ${cs.fontWeight}`
    sizes[key] = (sizes[key] ?? 0) + text.length
    total += text.length
  }
  const panels = [...document.querySelectorAll('.dv-groupview')].map((g) => {
    const r = g.getBoundingClientRect()
    return { area: `${Math.round(r.width * r.height / (W * H) * 100)}%`, title: g.querySelector('.dv-tab')?.textContent.trim() }
  })
  return {
    panels,
    text: Object.entries(sizes).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}: ${Math.round(v / total * 100)}%`),
  }
})()
```

Read the result against the rule: most text in one narrow size band at one weight, or the largest panel holding the least information, is a finding. Pair the numbers with a screenshot you opened.

## Density probe (`no-equal-weight-noise`)

Panel content renders in Dockview overlays, not inside `.dv-groupview`, so measure each panel by its region label. For each region, count leaf text elements inside its visible box and the ratio of its largest scroll container's content height to its visible height ("screens"). Report rows such as "Event log: 156 rows, 14.9 screens, ~9 visible". Large counts are a finding only when the default view lists raw records instead of what matters at the current moment; virtualization (DOM node count) is a performance question, not a reading one.

## Truthfulness under failure (`no-unknown-as-absent`, `no-shared-failure`, `no-dead-end-state`)

- Missing file: in the copied artifacts root, move one `perception.jsonl` or `world-events.jsonl` aside and rescan. Never edit the source capture. Restore it afterwards.
- Failed request: block one `/assets/...` URL (`agent-browser network route "**/perception.jsonl*" --abort`). A blocked request is an error, not a 404; test both.
- Slow request: delay one URL by several seconds and capture the in-progress state.

Capture the lane, the inspector, and any count that depends on the source.

## Transitions and keys (`no-input-hijack`, `no-lost-context`)

- Focus a button, press Space; focus the resource tree, press Backspace. Record both the control's response and the timeline's. Also click a button with the mouse and then press Space: the shortcut must still reach the timeline.
- When checking that a segment was or was not deleted, read the player lane rows (`[data-track-role="primary"]`), not page text; a data track such as "Visible · Airi" keeps the name on screen after Airi's lane is gone.
- Build a composition, change view settings, reload, and capture what survived.

## Production build

`pnpm --filter @proj-airi/recorder-minecraft-dashboard exec vite build --outDir <scratch>`. The dev server accumulates generated CSS in memory, so a missing utility or an invalid generated selector only shows in a production build. Use it to check that an icon or utility class is generated (grep the built CSS), not the dev `__uno.css`.

## Regression and ablation probe

Use when a change is meant to keep the render (refactor, cleanup, replacing hand-written CSS or widgets with existing ones), and to prove which part of a style is load-bearing.

1. Script the reference state end to end (open, reload, add the session, seek to a fixed tick, open what must be seen, move the pointer to a corner, blur focus) so it reproduces exactly.
2. Capture it twice and diff the two with `agent-browser diff screenshot --baseline <png> -t 0`. This noise floor must be 0 before any other diff means anything.
3. After each change, capture and diff against the baseline with `-t 0`. The default threshold (0.1) hides small color changes: it reported 0 for a tab background that changed from `#18181b` to `#1e1e1e` across 70k pixels.
4. Locate every differing cluster (`--output` writes a diff image) and explain it, or revert. Report the pixel count and the explanation per cluster.
5. For ablation, remove one rule or declaration at a time, wait for HMR, rerun the state, diff, restore. Zero difference in the reference state is necessary but not sufficient: states the script does not reach (several tabs in one group, hover, drag, pressed keys) still need a reason before a rule is deleted.
