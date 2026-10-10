# recorder-minecraft

`recorder-minecraft` is the Go command-line processor for recorder artifacts. Run it
directly from the repository root during development:

```sh
go run ./cmd/recorder-minecraft --help
go run ./cmd/recorder-minecraft plays list
go run ./cmd/recorder-minecraft plays list --output json
go run ./cmd/recorder-minecraft actions extract --help
go run ./cmd/recorder-minecraft scene extract --help
go run ./cmd/recorder-minecraft perception extract --help
go run ./cmd/recorder-minecraft render --help
```

Every processor accepts explicit input and output paths. Scene extraction uses
the headless Java extractor, writes only the final Scene Store V2 to the
requested destination, and removes its private runtime job after success.

`perception extract` reads a completed Play's `scene.sqlite3` read-only, plus
its metadata and event stream, and writes `perception.jsonl`: for every
sampled server tick, the entities and block entities the recorded player
could see. It is reconstructed actor perception. The header records input
digests and every assumption: FOV (`--fov`, default 70 vertical), aspect
(`--aspect`, default 16:9), sampling (`--interval-ticks`, default 4), and
distance (`--max-distance`, default 64 blocks, also capped by view distance).
Cells the scene does not know make a target undetermined rather than visible
or hidden. Regenerate the vanilla occluder table with
`hack/generate-perception-occluders` after a Minecraft version change.

`plays list` reads the configured Artifacts root without starting the catalog
server or Dashboard. It calculates read-only summaries for completed Plays and
keeps incomplete Plays visible without opening their live Event streams. Use
`--server-instance`, `--player`, `--started-at-or-after`, and `--started-before`
to limit the selected Plays before summary calculation.
