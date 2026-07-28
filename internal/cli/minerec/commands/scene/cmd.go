package scene

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
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
