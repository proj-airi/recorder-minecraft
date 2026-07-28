package main

import (
	"fmt"
	"os"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec"
)

func main() {
	command, err := minerec.New()
	if err == nil {
		err = command.Execute()
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "minerec: error: %v\n", err)
		os.Exit(2)
	}
}
