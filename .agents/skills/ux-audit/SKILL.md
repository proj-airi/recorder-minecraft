---
name: ux-audit
description: Audit one dashboard surface against the `ux` rules and land the result. Answers "does this surface do its job?" before listing defects, and reports each rule as a finding, a pass, or not checked. Use for UX audits, "is this panel readable", "why does this feel cluttered", comparing two layouts, and the required change verification before any dashboard UI change is declared complete. Produces a report and rule or case updates, not UI changes.
---

# ux-audit

An audit answers one question first: **does this surface answer its main question, readably, with true data?** Defects come after that verdict and explain it.

Audit one surface per run: a panel, the editor workspace at its default layout, or one flow such as "add a session and inspect a divergence".

## Inputs

- The surface and its **main question**, taken from [references/product.md](references/product.md). If the surface has no entry there, write one before auditing.
- The baseline: `git fetch`, then audit `origin/main @ <hash>` in a clean worktree. Name any uncommitted local changes that touch the surface instead of auditing them silently.
- Real data. Hierarchy and noise only show at realistic volume; an empty catalog proves nothing about reading.

## Steps

1. **Benchmark the class.** List what comparable tools give on this kind of surface ([product.md](references/product.md) names them). Write the expected capabilities before reading code; a capability that was never built leaves nothing to find in the code.
2. **Verdict.** Open the surface at its default state (L2). Write one line: "Main question answered? yes/no, because …". State where the answer is on screen, how large it is, and how many actions it takes to reach.
3. **Run the rules.** For every `ux` rule, record one of: finding (with evidence), pass (with evidence), or **not checked** (with the missing layer). A rule is never passed by default.
4. **Report** in the format below.
5. **Land** the result (see Landing).

### Layer requirements

| Layer | How | Required when |
| --- | --- | --- |
| L1 code | Read the surface and the backend that feeds it | Every audit |
| L2 render | Run the dashboard on real data; screenshot; run [probes](references/probes.md) | Always for the verdict. Always when the complaint is about reading, clutter, or "no structure". |
| L3 drive | Drive the flow; force failures; time transitions; press keys | Truthfulness under failure, transitions, shortcuts, persistence |

A complaint about readability with only L1 evidence is not an audit. Say "not checked (needs L2)" and stop short of a verdict.

## Change verification

Required before a UI change is declared complete (see `AGENTS.md`). It is the same audit, scoped to the change:

- **Surfaces**: every surface the change touches, each with its main question from [product.md](references/product.md).
- **Baseline**: the change's parent commit, against the change. Run both on the same real data, viewport, and reference state.
- **Layers**: L2 always: screenshots of each touched surface, opened. L3 for every rule the change claims to fix and every rule it could break (keys and focus, failure and missing states, persistence). A refactor or cleanup that should not change the render runs the regression probe in [probes.md](references/probes.md) with a zero-tolerance diff.
- **Checks**: the production build, not only the dev server, and the browser test suite.
- **Outcome**: the report below with a `Change:` line (commit or PR), every regression fixed or listed as a finding, and the landing done. A change is not complete while a touched surface is "not checked" at L2.

## Severity

- **Trust**: the screen states something false, or the user loses work. Includes every confirmed `no-unknown-as-absent` and `no-inert-action`.
- **Blocks**: the main question cannot be answered, or the user is stuck.
- **Friction**: slower or noisier than needed, but the answer is reachable.

Rank by severity, then by how many of the surface's users hit it.

## Report

```
# UX audit: <surface>

Baseline: origin/main @ <hash>   Change: <commit or PR, for change verification>   Layers: L1 ✅ · L2 ✅ <viewport, data> · L3 ⏳ <reason>
Main question: <one line>
Verdict: <yes/no>, because <one line>. Answer is <where>, <size>, <n actions>.
Class: <class>; compared with <tools>. Missing against the class: <list or none>.

## Strengths (keep these)
- <behavior> — <evidence> — why it matters

## Findings
| # | Severity | Rule | Finding | Layer + evidence | Fix |

## Not checked
| Rule | Missing layer | What would check it |

## Landed
- Cases added: <rule> ← <finding>
- Rules changed: <rule>: <what changed and why>, or "none"
```

Keep the report out of the repository unless asked. It describes one moment; what generalizes goes into the skills.

## Landing

An audit is done when its result is landed:

1. Every finding and strength becomes an entry under its rule in `ux/references/cases.md`, with baseline and evidence.
2. A defect no rule covers: first try widening an existing rule's high-signal shapes. Add a new rule only if the shape generalizes beyond this surface; follow the rule format in `ux`.
3. A rule that produced a wrong finding: tighten its **Prove it** or **Not a finding**. A wrong "missing" costs more than a missed finding.
4. Product facts that changed (main question, class, objects): update [references/product.md](references/product.md).

If nothing generalized, write "rules changed: none" in the report; an empty landing must be stated, not implied.

## Traps

- Reporting state-level bugs while the user asked about reading. Answer the verdict first.
- Judging hierarchy from class names (`text-[10px]`) instead of the render.
- Auditing on an empty or toy catalog.
- Citing a screenshot you did not open.
- Treating a tool error (stale element ref, wrong selector) as a product defect.
- Declaring one layout "better" without naming the success measure and observing it.
- Trusting a screenshot diff at the default threshold, or a dev-server stylesheet, as proof that nothing changed.
- Treating a pass in one run as stable. A check that depends on generated CSS or timing can fail one run in ten; rerun the suite before calling a flaky failure fixed, and say when the sample is too small to prove a cause.
