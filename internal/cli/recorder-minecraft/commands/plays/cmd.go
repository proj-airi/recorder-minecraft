package plays

import "github.com/spf13/cobra"

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "plays",
		Short: "Inspect recorded Plays",
	}
}
