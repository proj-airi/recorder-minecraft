package perception

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	perceptionextract "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/perception/extract"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, perceptionextract.NewCommand)
	root.AddCommand(parent)
}
