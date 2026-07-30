# recorder-minecraft

`recorder-minecraft` is the Go command-line processor for recorder artifacts. Run it
directly from the repository root during development:

```sh
go run ./cmd/recorder-minecraft --help
go run ./cmd/recorder-minecraft actions extract --help
go run ./cmd/recorder-minecraft scene extract --help
go run ./cmd/recorder-minecraft render --help
```

Every processor accepts explicit input and output paths. Scene extraction uses
the headless Java extractor, writes only the final Scene Store V2 to the
requested destination, and removes its private runtime job after success.
