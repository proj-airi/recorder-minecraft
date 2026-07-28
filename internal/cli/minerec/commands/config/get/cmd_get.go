package get

import (
	"fmt"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get FIELD",
		Short: "Print one resolved configuration field",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			config, err := configs.Load(configPath)
			if err != nil {
				return err
			}
			var value string
			switch args[0] {
			case "server.name":
				value = config.Server.Name
			case "server.instance_id":
				value = config.Server.InstanceID
			case "paths.artifacts":
				value = config.Paths.Artifacts
			case "paths.runtime":
				value = config.Paths.Runtime
			default:
				return fmt.Errorf("unsupported configuration field %q", args[0])
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), value)
			return err
		},
	}
}
