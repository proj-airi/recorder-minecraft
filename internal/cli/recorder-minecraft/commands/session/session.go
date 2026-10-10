package session

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	sessionalign "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/session/align"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, sessionalign.NewCommand)
	root.AddCommand(parent)
}
