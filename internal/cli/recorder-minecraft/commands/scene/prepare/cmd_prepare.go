package prepare

import (
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models"
	"github.com/proj-airi/recorder-minecraft/internal/models/scenes"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var options scenes.Options
	var fromTick int64
	var toTick int64
	cmd := &cobra.Command{
		Use:   "prepare",
		Short: "Prepare one scene extraction job",
		Long: `Prepare the strict SceneExtractionJob ProtoJSON and subject pose stream consumed by
the dedicated-server scene extractor. Capture and replay identities are
verified before the owned runtime directory is published.`,
		Example: `  recorder-minecraft scene prepare \
    --metadata PLAY/metadata.json \
    --events PLAY/capture/events.jsonl \
    --replay PLAY/capture/replay.zip`,
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
	cmd.Flags().StringVar(&options.Replay, "replay", "", "Flashback replay ZIP input")
	cmd.Flags().StringVar(&options.Output, "job-output", "", "Explicit private job directory")
	cmd.Flags().Int64Var(&fromTick, "from-tick", 0, "First server tick to include")
	cmd.Flags().Int64Var(&toTick, "to-tick", 0, "Last server tick to include")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace an existing recorder-owned job directory")
	for _, name := range []string{"metadata", "events", "replay"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func run(cmd *cobra.Command, options scenes.Options) func(do.Injector) error {
	return func(injector do.Injector) error {
		extractor, err := do.Invoke[*scenes.Extractor](injector)
		if err != nil {
			return err
		}
		job, err := extractor.Prepare(options)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintf(cmd.OutOrStdout(), "Prepared scene extraction job %s\n", job.Manifest)
		return err
	}
}
