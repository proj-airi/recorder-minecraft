package actions

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	actionsextract "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/actions/extract"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, actionsextract.NewCommand)
	root.AddCommand(parent)
}
