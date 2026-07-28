package actions

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	actionsextract "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/actions/extract"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, actionsextract.NewCommand)
	root.AddCommand(parent)
}
