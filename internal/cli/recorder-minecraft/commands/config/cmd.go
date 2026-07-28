package config

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "config",
		Short:   "Inspect recorder configuration",
		GroupID: command.WorkspaceGroup,
		Long:    "Inspect the resolved recorder configuration using subcommands such as 'recorder-minecraft config view'.",
	}
}
