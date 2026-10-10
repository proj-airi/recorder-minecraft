package session

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "session",
		Short:   "Process a world session together with its Plays",
		GroupID: command.ProcessingGroup,
		Long:    "Session processors read one world session stream and several Plays of the same session_id on the shared server tick timeline.",
	}
}
