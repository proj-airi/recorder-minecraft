# recorder-minecraft

`recorder-minecraft` is the Go command-line processor for recorder artifacts. Run it
through the repository task boundary:

```sh
pixi run recorder-minecraft --help
pixi run recorder-minecraft actions extract --help
pixi run recorder-minecraft scene extract --help
pixi run recorder-minecraft render --help
```

Every processor accepts explicit input and output paths. Scene extraction uses
the dedicated-server extractor, writes only the final Scene Store V2 to the
requested destination, and removes its private runtime job after success.
