package scene

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "scene",
		Short:   "Extract and inspect random-access scene stores",
		GroupID: command.ProcessingGroup,
		Long:    "Extract and inspect Scene Store V2 SQLite artifacts.",
	}
}
