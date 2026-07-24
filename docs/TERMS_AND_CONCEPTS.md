# Terms and Concepts

This document defines the domain boundaries, component responsibilities, and
shared terminology used by the current mc-play-recorder implementation. It
describes what exists in the repository today. Product directions that have not
been implemented are not presented as existing capabilities.

The following conventions apply:

- A **preferred term** is the name new code, APIs, logs, and documentation should
  use.
- A **legacy term** is retained only to explain existing symbols or support
  existing files. Do not extend its use.
- A status must identify the object it describes. Use `render job: partial`
  instead of an unqualified `partial`.
- `player_uuid`, `connection_id`, `segment_id`, `dataset_id`, and `job_id`
  belong to different identity spaces. They cannot be derived from or substituted
  for one another.

## Domains

| Domain | Owns | Inputs | Outputs | Boundary |
| --- | --- | --- | --- | --- |
| Minecraft server runtime | Minecraft server process, server ticks, player entities, and network connection lifecycles | Player client connections, server configuration, and mods | Server callbacks, packets, world state, and player state | Does not own Datasets, the render queue, or the Dashboard |
| Recorder | Server-side events observed by the Recorder mod, session identity, global sequence, and epoch publication | Minecraft server callbacks and applied serverbound packets | Capture sessions, epoch event streams, timeline markers, and replay identity metadata | Does not generate Flashback ZIPs, launch the renderer, or manage the Dashboard |
| Replay | ServerReplay/Flashback archive creation, rotation, and final ZIP publication | Minecraft client-visible packets and replay identity metadata supplied by the Recorder | Flashback replay archives | Archive rotation is independent of Recorder epoch rotation; the Recorder cannot request publication of a final ZIP |
| Capture processing | Capture session inspection, active-prefix snapshots, export, and retention | Recorder capture filesystem and completed replay filesystem | Verified capture views, Dataset V2, and storage reports | Reads or copies Recorder sources only; a consumer snapshot must never be written back as a Recorder source |
| Dataset | Observation states, transition samples, actions, modality references, and provenance | Verified capture data and optional RGB/scene attachments | Atomically published Dataset V2 directory | Python verifies Dataset identity, selection, and file integrity |
| Render control | Render job, attempt, lease, dispatch, and finalization state | Dashboard render requests and Dataset or replay artifact identity | Durable queue state, RabbitMQ job messages, renderer plans, and attachment results | Does not produce pixels directly; paths supplied by a browser or worker are never trusted |
| Renderer runtime | GUI client playback and first-person RGB generation for one replay segment | Server-authored portable requests, verified replay ZIPs, and structured HUD sidecars | PNG frames, frame indexes, and worker results | Requires the Minecraft client and Fabric renderer mod; `no_gui` does not mean headless |
| Scene extraction | Replay packet reduction and random-access scene store generation | Verified replay segments, authoritative subject poses, and scene jobs | Scene streams and `scene-v1.sqlite3` | Uses a dedicated-server executable and does not depend on a GUI client |
| Viewer | Dataset catalog, indexed queries, and trajectory/sample/frame/scene inspection | Verified Dataset V2 publications and durable render imports | Bounded HTTP responses and interactive frontend views | Does not modify capture or replay sources and does not own render execution |
| Storage and retention | Capture/replay quotas, pinning, and safe deletion | Verified sealed epochs and stable completed replay archives | Storage status and eviction decisions | Does not delete active or incomplete epochs, changing archives, world data, or Datasets |
| Operator tooling | Local commands, Compose lifecycle, and GUI consumer startup | Repository checkout, configuration, and desktop environment | Running services and persistent GUI consumers | The Dashboard does not manage Docker Compose or the Minecraft server lifecycle |

## Components

| Component | Runtime | Domain | Responsibility | Does not own |
| --- | --- | --- | --- | --- |
| `mods/recorder-mod` | Minecraft server JVM | Recorder | Records server-visible events, manages capture sessions and epochs, and injects identity into replay metadata | Flashback ZIP finalization, Dataset export, or render jobs |
| ServerReplay and Flashback integration | Minecraft server JVM | Replay | Records and publishes Flashback replay ZIPs by player and segment | Recorder event JSONL or the Dashboard queue |
| `ArtifactCatalog` | Python | Capture processing | Scans and verifies capture sessions and completed replay archives | Dataset content or render execution |
| `CaptureSnapshot` | Python | Capture processing | Creates a bounded, verified append-prefix snapshot for a connection that has ended | Recorder source publication |
| `export_episode` | Python | Dataset | Atomically builds Dataset V2 from verified capture data and joins optional modalities | GUI rendering |
| `DatasetViewer` | Python | Viewer | Verifies Dataset V2, maintains a SQLite byte-offset index, and queries by opaque ID | Dataset authoring or queue scheduling |
| Dashboard HTTP server | Python | Viewer / Render control | Serves the static frontend and authenticated HTTP API from the same origin | GUI worker process management |
| `apps/dashboard` | Browser / Vue | Viewer | Displays artifacts, Datasets, render jobs, and viewer interactions | Filesystem paths, lease tokens, or renderer commands |
| `RenderQueueStore` | Python / SQLite | Render control | Stores render jobs, attempts, workers, leases, and terminal results in the Compose `render-control` volume | RabbitMQ delivery guarantees or PNG files |
| `RenderPreparer` | Python service | Render control | Verifies a replay, snapshots a connection, and generates and binds a Dataset for a replay-artifact request | GUI client |
| `render-dispatcher` | Python service | Render control | Sends publishable queued jobs to RabbitMQ and records a dispatch marker | Job authority or render execution |
| RabbitMQ broker adapter | RabbitMQ / Python | Render control | Delivers a wake-up message for an existing durable render job | Job state, attempt state, or Dataset identity |
| `render-worker` | Python on a GUI host | Renderer runtime | Continuously consumes RabbitMQ messages, calls the Dashboard render-control endpoint, runs segment invocations serially, and takes the next job after completion | Queue SQLite access or Minecraft server management |
| `RenderRpcService` | Python | Render control | Validates token-authenticated worker actions, creates fenced plans, and provides source/request/finalize operations | Browser API or renderer execution |
| `mods/renderer-mod` | Minecraft client JVM | Renderer runtime | Opens a Flashback replay, synchronizes first-person/HUD state, and writes PNG/index/result files | Queue state or Dataset replacement |
| Scene extractor executable | Java dedicated-server process | Scene extraction | Executes a scene job and produces a verifiable scene stream | GUI RGB or the Dashboard |
| `storage-monitor` | Python service | Storage and retention | Periodically checks capture/replay quotas and performs safe eviction | World, Dataset, or render import retention |
| `hack/*` wrappers | Host shell | Operator tooling | Builds, starts, and inspects local Compose, RabbitMQ, Dashboard, and related services | Product domain state |

## Identity Hierarchy

| Identity | Scope | Meaning |
| --- | --- | --- |
| `session_id` | One Minecraft server-process capture run | Stable identity of a capture session |
| `epoch_index` | One capture session | Consecutive index in the Recorder sidecar stream |
| `player_uuid` | Minecraft account/player | Player identity that remains stable across reconnects |
| `connection_id` | One join-to-leave interval | Independent identity for each connection made by the same player |
| `segment_id` | One completed replay archive | UUID of a ServerReplay/Flashback replay segment |
| `dataset_id` | One direct `*.dataset` directory name under an exports root | Opaque ID used by the Viewer; it is not currently a content hash |
| `sample_id` | One Dataset observation/transition location | Opaque sample identity used by the Viewer |
| `job_id` | One durable end-to-end render request | Identity of a SQLite render job |
| `attempt_id` | One worker lease generation | One fenced execution of a job by a worker |
| `request_id` | One replay-segment renderer request | Identity of a portable request |

## Capture Terms

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Capture session | Complete session recorded by the Recorder during one Minecraft server process run | `episode` is an existing Python legacy alias and must not imply a different object |
| Epoch | Append-only JSONL slice managed by the Recorder within a capture session | Do not use an unqualified `segment` for an epoch |
| Active epoch | Epoch whose `events.jsonl.inprogress` is still being written | `inprogress` is a file publication state, not a separate domain object |
| Sealed epoch | Epoch with published `events.jsonl` and manifest files that pass count/bytes/SHA-256 verification | `sealed` describes epoch integrity only; it does not mean the session has ended |
| Capture prefix snapshot | Private, verified snapshot of a stable append-only prefix created by a consumer for a connection that has ended | Do not call it a Recorder seal; `rotation_reason=consumer_snapshot` does not alter the source |
| Capture session end | Clean, incomplete, or failed shutdown result represented by `session_end.json` | Do not infer session end merely because no active epoch is visible |
| Player connection | Interval from one `player_join` to its corresponding `player_leave` | A player UUID cannot replace a connection ID |

The current export and retention contracts still depend on sealed epochs. A
capture prefix snapshot is a consumer capability for reading the stable prefix
of a connection that has ended. It does not remove the Recorder epoch
publication protocol.

## Replay Terms

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Replay archive | Final, published, and verifiable Flashback ZIP/MCPR file produced by ServerReplay | Do not call an incomplete ZIP an archive |
| Replay segment | One replay archive with a `segment_id` and `segment_ordinal` | Always include the `replay` qualifier |
| Replay identity metadata | `mc_recorder` identity inside `arcade_replay_meta.json` in the ZIP | It is neither a Dataset manifest nor queue state |
| Timeline marker | Recorder payload inside a replay that aligns replay ticks with global server ticks | Do not replace it with wall-clock time or file timestamps |
| Replay coverage | Global tick intersection actually contained by one replay segment | Coverage gaps must remain missing; do not interpolate or fabricate them |

Replay segment rotation and Recorder epoch rotation are independent. They may
use similar durations, but there is no one-to-one relationship between them.

## Dataset Terms

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Observation state | Authoritative `player_state` for a selected player connection at the end of a tick | An unqualified `state` may also refer to queue, worker, or scene state |
| Action record | Reconstructed `control_state` or applied serverbound packet | It is not a raw keyboard/mouse event or a Flashback archive `Action` |
| Transition sample | `state[t] + reconstructed control/ordered packets -> state[t+1]` | A terminal observation has no transition sample |
| Modality | Optional RGB or scene data joined exactly to an observation tick | A missing modality is a valid, explicit state |
| RGB frame | Single-tick PNG and corresponding `frames.jsonl` row produced by the Renderer | It is not an original player-client pixel capture |
| Scene frame | Logical client-visible scene snapshot at a timeline marker | It is not an RGB image |
| Dataset selection | Export request fixed to session/player/connection/tick bounds | It is not the same as replay segment coverage |
| Dataset manifest | File inventory, selection, and source provenance for the Dataset core | The Renderer must not modify it; derived attachment provenance belongs in the attachment manifest |
| Dataset viewer index | Rebuildable SQLite byte-offset cache under `.mc-recorder` | It is not a source of truth |

An RGB attachment does not modify the Dataset core. It is atomically published
at `exports/.dataset-attachments/<dataset_id>/rgb.json` and binds the Dataset
manifest and `samples.jsonl` SHA-256 values. The Viewer joins this derived index
to core observations by exact identity and tick.

## Render Terms

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Render request | Product operation through which a user requests RGB generation in the Dashboard | Once persisted, call it a render job |
| Render job | One end-to-end RGB request stored in SQLite | Do not use it for a single-segment Java invocation |
| Render job message | RabbitMQ wake-up/delivery message for an existing render job | It is not another job or a source of truth |
| Render attempt | Execution with a generation and lease token after a worker claims a job | An attempt failure does not create a new job |
| Render plan | Sources, identity, paths, and integrity envelope fixed by Server/Python for an attempt | It is not authored by the browser or worker |
| Portable render request | Machine-path-free renderer input for one replay segment | Distinguish it from the end-to-end render request |
| Renderer invocation | Local `render-job.json` materialized and executed by a worker for one replay segment | Existing code may call this a local `render job` |
| Render bundle | Hash-indexed portable output before worker upload | It is not the final Dataset |
| Durable render import | Canonical files verified and published by Python under `exports/render-jobs/<job>/<segment>` | Distinguish it from the worker's ephemeral workspace |
| RGB attachment | Association of durable RGB references with a Dataset through an exact identity/tick join | Does not rewrite the Dataset core or copy/synthesize missing frames |
| Full coverage | Every selected sample has verified RGB | The queue terminal state is `complete` |
| Partial coverage | At least one valid render import exists, but selected samples still contain RGB gaps | The queue terminal state is `partial`, which is a successful result |
| No coverage | One replay segment has no intersection with the requested range | A segment may return `no_coverage` without failing the entire job |
| GUI-hidden render | GUI renderer output with the Minecraft HUD hidden by `no_gui=true` | Do not call it headless; a graphical environment is still required |

## Scene Terms

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Scene extraction | Reconstruction of random-access scene data through Minecraft packet codecs and a reducer | Do not call it RGB rendering |
| Scene extractor executable | Dedicated-server CLI distribution built from `mods/scene-extractor-mod` | The directory contains `mod` in its name, but Python invokes an executable |
| Scene stream | Intermediate frame/change/blob spool written by the Extractor | It is not the final Dataset |
| Scene store | Compacted random-access value stored in `scene-v1.sqlite3` | Does not require a GUI client |
| Subject present | The scene reducer found the selected subject entity at a marker | Does not by itself establish complete world/scene coverage |
| Coverage complete | Stronger guarantee that a Viewer or training consumer can safely use the selected scene region | Requires explicit completeness criteria; subject presence alone is insufficient |

## Viewer and Timeline Terms

This section includes both current query concepts and product terms reserved for
future Viewer work. A reserved term does not imply that the repository already
implements the corresponding editing, annotation, or persistence capability.

| Preferred term | Definition | Avoid or qualify |
| --- | --- | --- |
| Dataset view | Rebuildable query result produced by applying filters, sorting, ranges, and field selection to a Dataset | Does not copy the underlying Dataset or create a new `dataset_id` |
| Timeline | Interactive view that aligns multiple data tracks on the global server tick axis | Do not assume it is equivalent to video time in seconds or replay-local ticks |
| Track | Timeline lane for data with the same type and time semantics, such as RGB coverage, actions, or validation issues | It is not a new physical file format inside a Dataset |
| Playhead | Single global server tick currently selected in the Viewer | Does not imply that the Dataset has been modified or split |
| Range selection | Temporary or persistent Viewer selection with an inclusive start tick and exclusive end tick | Do not call it a sealed Dataset |
| Coverage interval | Continuous tick range in which a modality or replay segment is actually available | Distinguish it from a user range selection |
| Timeline gap | Explicit interval on the expected timeline with no corresponding source or modality | Do not fill it to simulate continuous coverage |
| Annotation | Operator-authored metadata attached to a sample, tick, or range | Must not be written back to Recorder source events |
| Derived asset | Independent output computed from a Dataset or source, such as an RGB import, scene store, thumbnail, or validation report | Do not combine it with Dataset core files as though they were outputs of one pipeline |
| Attachment | Record that associates a derived asset with a Dataset through identity, tick range, and provenance | Does not require rewriting the Dataset core |
| Split | Creation of two adjacent, non-destructive selections inside one range | Does not mean Recorder rotation or replay ZIP finalization |
| Trim | Adjustment of selection start/end bounds | Does not delete source ticks |
| Concat | Logical sequence that references multiple selections in explicit order and permits source gaps | Does not fabricate continuous Minecraft ticks across a gap |
| Merge | Domain operation that combines compatible metadata, annotations, or adjacent ranges | Do not use it as a synonym for `concat`; define a conflict policy |
| Materialize | Publication of a Dataset view or logical sequence as a new, independently verified Dataset | Distinguish it from saving only a selection or reference |

Viewer split, trim, and concat operations should use a non-destructive edit list.
The list stores only source Dataset identity, tick bounds, order, and provenance.
A new Dataset should be materialized only when the user explicitly exports it.
This allows one source to support multiple views and edit versions without
allowing GUI operations to change the training-data source of truth.

## Status Vocabulary

| Object | Allowed or preferred states | Meaning |
| --- | --- | --- |
| Capture session | `open`, `complete`, `incomplete`, `failed`, `empty` | Server-process capture lifecycle |
| Epoch | `active`, `sealed`, `incomplete` | Recorder sidecar publication/integrity |
| Replay archive | `saving`, `completed`, `invalid` | Physical ZIP lifecycle; the current catalog primarily displays completed archives |
| Render job | `preparing_dataset`, `queued`, `downloading`, `rendering`, `uploading`, `verifying`, `attaching`, `complete`, `partial`, `failed`, `canceled` | End-to-end queue state |
| Render attempt | `leased`, `uploaded`, `failed`, `deferred`, `expired`, `canceled` | One worker execution and lease |
| Renderer invocation | `prepared`, `complete`, `no_coverage`, `failed` | One replay segment execution |

`published` must identify its object:

- `epoch published` means the Recorder atomically published the sealed epoch
  files.
- The existing queue field `published_at` means the job message was sent to
  RabbitMQ. New APIs and documentation should call this `dispatched_at`.

## Known Ambiguities and Migration Guidance

| Existing wording | Problem | Preferred wording |
| --- | --- | --- |
| Episode | Refers to the same object as a capture session but implies another lifecycle | Capture session |
| Segment | Refers to replay archives, snapshot sources, and writer internals | Replay segment, snapshot source epoch, or open epoch writer |
| Render job | Refers to both a queue job and local Java input | Render job or renderer invocation |
| Task | Can be mistaken for another durable work item | Render job message |
| Worker | Refers to both the GUI render process and a Recorder-internal writer thread | Render worker or epoch writer thread |
| Frame | Refers to both a PNG and a scene snapshot | RGB frame or scene frame |
| Complete | Is reused across multiple lifecycles | Capture complete, render job complete, or renderer invocation complete |
| Published | Refers to both filesystem publication and broker dispatch | Epoch published or job dispatched |
| Immutable Dataset | The Dataset core publication does not change after RGB rendering | Derived attachment publication |
| Headless render | `no_gui` still launches the Minecraft client | GUI-hidden render |
| SSH RPC | The code no longer provides SSH or a hidden RPC CLI; the consumer calls an in-process control service directly | In-process render control action; do not imply remote transport |

## Core Relationships

```text
Capture session
  |- Epoch 0..N
  |    `- Recorder events
  `- Player
       `- Connection 0..N
            |- Replay segment 0..N
            `- Dataset selection
                 |- Observation states
                 |- Transition samples
                 |- Action records
                 |- Optional scene frames
                 `- Optional RGB frames

Render request
  `- Render job
       `- Render attempt 0..N
            `- Renderer invocation per replay segment
                 `- Durable render import
                      `- RGB attachment
```

## Invariants

1. Cross-file joins use exact
   `(session_id, player_uuid, connection_id, global server tick)` identity.
2. Samples never cross a connection boundary.
3. Replay segment and Recorder epoch boundaries are independent.
4. Missing replay, RGB, or scene coverage remains explicit; consumers must not
   interpolate it into apparently complete evidence.
5. RabbitMQ delivery is not job authority. SQLite render queue state and fenced
   attempt leases decide whether a worker may finalize.
   In Compose, that SQLite value remains on Docker's native `render-control`
   volume and GUI workers mutate it only through the Dashboard endpoint.
6. Browser requests do not supply filesystem paths, commands, JVM flags, lease
   tokens, or authoritative source identity.
7. Worker-local paths and results are provenance only until Python imports and
   verifies the bundle.
8. Recorder source files are append-only while active and integrity-checked after
   epoch publication. Consumer snapshots do not mutate Recorder source.
9. Dataset viewer indexes are disposable caches and can always be rebuilt from
   verified Dataset files.
10. Scene and RGB are separate modalities produced by separate pipelines.
