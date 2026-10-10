package perception

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "perception",
		Short:   "Reconstruct what the recorded player could see",
		GroupID: command.ProcessingGroup,
		Long:    "Reconstruct actor-perception visibility of entities and block entities from a Scene Store V2.",
	}
}
