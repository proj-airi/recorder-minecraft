package view

import (
	"encoding/json"
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "view",
		Short: "Display the resolved recorder configuration",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			config, err := configs.Load(configPath)
			if err != nil {
				return err
			}
			if output != "json" {
				return fmt.Errorf("unsupported output format %q; use json", output)
			}
			encoder := json.NewEncoder(cmd.OutOrStdout())
			encoder.SetIndent("", "  ")
			return encoder.Encode(config)
		},
	}
	cmd.Flags().StringVarP(&output, "output", "o", "json", "Output format (json)")
	return cmd
}
