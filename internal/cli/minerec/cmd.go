package minerec

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands"
	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	root := &cobra.Command{
		Use:   "minerec",
		Short: "Process Minecraft gameplay recordings",
		Long: `minerec processes explicit Minecraft recorder artifacts.

It validates immutable capture inputs before reconstructing player actions,
extracting random-access scenes, or rendering first-person frames.`,
		SilenceErrors: true,
		SilenceUsage:  true,
	}
	root.PersistentFlags().StringP("config", "c", configs.DefaultPath, "Path to the recorder TOML configuration")
	root.AddGroup(
		&cobra.Group{ID: command.WorkspaceGroup, Title: "Workspace Commands:"},
		&cobra.Group{ID: command.ProcessingGroup, Title: "Processing Commands:"},
		&cobra.Group{ID: command.OtherGroup, Title: "Other Commands:"},
	)
	commands.Register(root)
	return root
}

// New builds the command tree without starting the runtime dependency graph.
func New() (*cobra.Command, error) {
	return NewCommand(), nil
}
