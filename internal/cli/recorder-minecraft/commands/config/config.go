package config

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	configget "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/config/get"
	configview "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/config/view"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, configget.NewCommand, configview.NewCommand)
	root.AddCommand(parent)
}
