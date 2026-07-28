package version

import (
	"fmt"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "version",
		Short:   "Print minerec version information",
		GroupID: command.OtherGroup,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := fmt.Fprintln(cmd.OutOrStdout(), "minerec v0.1.0")
			return err
		},
	}
}
