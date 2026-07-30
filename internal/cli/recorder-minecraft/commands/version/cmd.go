package version

import (
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

var Value = "dev"

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "version",
		Short:   "Print recorder-minecraft version information",
		GroupID: command.OtherGroup,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := fmt.Fprintf(cmd.OutOrStdout(), "recorder-minecraft %s\n", Value)
			return err
		},
	}
}
