package initialize

import (
	"fmt"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var acceptEULA bool
	var overwrite bool
	cmd := &cobra.Command{
		Use:     "init",
		Short:   "Create recorder configuration and workspace directories",
		GroupID: command.WorkspaceGroup,
		Long: `Create recorder.toml with one stable server instance identity.

The artifacts and private runtime directories declared by the new
configuration are created at the same time.`,
		Example: "  minerec init\n  minerec init --accept-eula",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			path, err := configs.Initialize(configPath, acceptEULA, overwrite)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Initialized %s\n", path)
			return err
		},
	}
	cmd.Flags().BoolVar(&acceptEULA, "accept-eula", false, "Record acceptance of the Minecraft EULA")
	cmd.Flags().BoolVar(&overwrite, "overwrite", false, "Replace an existing regular configuration file")
	return cmd
}
