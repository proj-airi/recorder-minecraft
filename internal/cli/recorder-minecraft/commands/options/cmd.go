package options

import (
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "options",
		Short:   "Print the global command-line options",
		GroupID: command.OtherGroup,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if _, err := fmt.Fprintln(cmd.OutOrStdout(), "The following options can be passed to any command:"); err != nil {
				return err
			}
			if _, err := fmt.Fprintln(cmd.OutOrStdout()); err != nil {
				return err
			}
			_, err := fmt.Fprint(cmd.OutOrStdout(), cmd.Root().PersistentFlags().FlagUsages())
			return err
		},
	}
}
