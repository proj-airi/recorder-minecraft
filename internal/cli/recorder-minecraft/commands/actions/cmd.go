package actions

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "actions",
		Short:   "Reconstruct player action streams",
		GroupID: command.ProcessingGroup,
		Long:    "Reconstruct ordered player actions from validated capture event records.",
	}
}
