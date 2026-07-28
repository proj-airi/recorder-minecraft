package extract

import (
	"fmt"
	"path/filepath"

	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/proj-airi/mc-play-recorder/internal/models"
	"github.com/proj-airi/mc-play-recorder/internal/models/actions"
	"github.com/proj-airi/mc-play-recorder/pkg/filelock"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var options actions.Options
	var fromTick int64
	var toTick int64
	cmd := &cobra.Command{
		Use:   "extract",
		Short: "Write one connection action stream",
		Long: `Write actions.jsonl from generated control_state and packet_apply records.

The metadata and event stream must describe the same completed connection.
Input identity, tick bounds, ordering, and stability are verified before the
staged output is atomically published.`,
		Example: `  minerec actions extract \
    --metadata PLAY/metadata.json \
    --events PLAY/capture/events.jsonl \
    --output PLAY/actions.jsonl`,
		Args: cobra.NoArgs,
		PreRunE: func(cmd *cobra.Command, _ []string) error {
			if cmd.Flags().Changed("from-tick") {
				options.FromTick = &fromTick
			}
			if cmd.Flags().Changed("to-tick") {
				options.ToTick = &toTick
			}
			return nil
		},
		RunE: func(cmd *cobra.Command, _ []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			return command.Run(cmd.Context(), run(cmd, options), configs.Package(configPath), models.Package)
		},
	}
	cmd.Flags().StringVar(&options.Metadata, "metadata", "", "Completed ServerMetadata ProtoJSON input")
	cmd.Flags().StringVar(&options.Events, "events", "", "CaptureEvent ProtoJSON lines input")
	cmd.Flags().StringVarP(&options.Output, "output", "o", "", "PlayerAction ProtoJSON lines output")
	cmd.Flags().Int64Var(&fromTick, "from-tick", 0, "First server tick to include")
	cmd.Flags().Int64Var(&toTick, "to-tick", 0, "Last server tick to include")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace an existing PlayerAction JSONL output")
	for _, name := range []string{"metadata", "events", "output"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func run(cmd *cobra.Command, options actions.Options) func(do.Injector) error {
	return func(injector do.Injector) error {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return err
		}
		service, err := do.Invoke[*actions.Service](injector)
		if err != nil {
			return err
		}
		return filelock.With(filepath.Join(config.Paths.Runtime, "operation.lock"), "actions_extract", func() error {
			result, err := service.Extract(options)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Extracted %d actions for ticks %d..%d to %s\n", result.RecordCount, result.FirstTick, result.LastTick, result.Output)
			return err
		})
	}
}
