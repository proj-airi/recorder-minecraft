package plays

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	listcmd "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/plays/list"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	parent.GroupID = command.WorkspaceGroup
	command.Register(parent, listcmd.NewCommand)
	root.AddCommand(parent)
}
