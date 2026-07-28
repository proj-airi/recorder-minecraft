package config

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	configget "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/config/get"
	configview "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/config/view"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, configget.NewCommand, configview.NewCommand)
	root.AddCommand(parent)
}
