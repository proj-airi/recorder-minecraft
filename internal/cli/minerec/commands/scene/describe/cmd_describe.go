package describe

import (
	"encoding/json"
	"fmt"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/models"
	"github.com/proj-airi/mc-play-recorder/internal/models/scenes"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "describe SCENE",
		Short: "Show metadata and row counts for a scene store",
		Long: `Show the identity, tick range, and table counts of one Scene Store V2.

The SQLite database is opened in read-only mode and is never migrated.`,
		Example: "  minerec scene describe PLAY/scene.sqlite3\n  minerec scene describe PLAY/scene.sqlite3 -o json",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return command.Run(cmd.Context(), run(cmd, args[0], output), models.Package)
		},
	}
	cmd.Flags().StringVarP(&output, "output", "o", "json", "Output format (json)")
	return cmd
}

func run(cmd *cobra.Command, path, output string) func(do.Injector) error {
	return func(injector do.Injector) error {
		reader, err := do.Invoke[*scenes.Reader](injector)
		if err != nil {
			return err
		}
		description, err := reader.Describe(cmd.Context(), path)
		if err != nil {
			return err
		}
		if output != "json" {
			return fmt.Errorf("unsupported output format %q; use json", output)
		}
		encoder := json.NewEncoder(cmd.OutOrStdout())
		encoder.SetIndent("", "  ")
		return encoder.Encode(description)
	}
}
