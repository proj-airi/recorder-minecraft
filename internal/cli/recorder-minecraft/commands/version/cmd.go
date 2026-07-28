package version

import (
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "version",
		Short:   "Print recorder-minecraft version information",
		GroupID: command.OtherGroup,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := fmt.Fprintln(cmd.OutOrStdout(), "recorder-minecraft v0.1.0")
			return err
		},
	}
}
