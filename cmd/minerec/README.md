# minerec

`minerec` is the Go command-line processor for recorder artifacts. Run it
through the repository task boundary:

```sh
pixi run minerec --help
pixi run minerec actions extract --help
pixi run minerec scene extract --help
pixi run minerec render --help
```

Every processor accepts explicit input and output paths. Scene extraction uses
the dedicated-server extractor, writes only the final Scene Store V2 to the
requested destination, and removes its private runtime job after success.
