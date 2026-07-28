package main

import (
	"fmt"
	"os"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft"
)

func main() {
	command, err := recorderminecraft.New()
	if err == nil {
		err = command.Execute()
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "recorder-minecraft: error: %v\n", err)
		os.Exit(2)
	}
}
