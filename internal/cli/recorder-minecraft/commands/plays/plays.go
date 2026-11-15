package plays

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	listcmd "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/plays/list"
	locatecmd "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/plays/locate"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	parent.GroupID = command.WorkspaceGroup
	command.Register(parent, listcmd.NewCommand, locatecmd.NewCommand)
	root.AddCommand(parent)
}
