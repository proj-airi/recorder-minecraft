//go:build ignore

package main

import (
	"log"

	"entgo.io/ent/entc"
	"entgo.io/ent/entc/gen"
	"github.com/proj-airi/mc-play-recorder/pkg/entsqlite"
)

func main() {
	// NOTICE: This follows Ent's extension-enabled generator entrypoint from
	// `https://github.com/ent/ent/blob/e0ba79d911cca949468293d4e2899d4c61cfc823/entc/entc.go#L200-L208`.
	err := entc.Generate("./schema", &gen.Config{}, entc.Extensions(entsqlite.NewExtension(2)))
	if err != nil {
		log.Fatal("generate Scene Store Ent code: ", err)
	}
}
