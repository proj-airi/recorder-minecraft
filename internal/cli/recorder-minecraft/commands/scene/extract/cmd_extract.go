package extract

import (
	"fmt"
	"path/filepath"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/datastore"
	"github.com/proj-airi/recorder-minecraft/internal/models"
	"github.com/proj-airi/recorder-minecraft/internal/models/scenes"
	"github.com/proj-airi/recorder-minecraft/pkg/filelock"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var options scenes.Options
	var prepareOnly bool
	var fromTick int64
	var toTick int64
	cmd := &cobra.Command{
		Use:   "extract",
		Short: "Write a random-access Scene Store V2",
		Long: `Validate a completed capture and Flashback replay, run the dedicated
server extractor, and atomically compact its private stream into scene.sqlite3.
Private job data is removed after a successful extraction.`,
		Example: `  recorder-minecraft scene extract \
    --metadata PLAY/metadata.json \
    --events PLAY/capture/events.jsonl \
    --replay PLAY/capture/replay.zip \
    --output PLAY/scene.sqlite3`,
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
			output := options.Output
			options.Output = ""
			if prepareOnly {
				return command.Run(cmd.Context(), runPrepare(cmd, options), configs.Package(configPath), models.Package)
			}
			return command.Run(cmd.Context(), run(cmd, options), configs.Package(configPath), datastore.Package(output, options.Overwrite), models.Package)
		},
	}
	cmd.Flags().StringVar(&options.Metadata, "metadata", "", "Completed ServerMetadata ProtoJSON input")
	cmd.Flags().StringVar(&options.Events, "events", "", "CaptureEvent ProtoJSON lines input")
	cmd.Flags().StringVar(&options.Replay, "replay", "", "Flashback replay ZIP input")
	cmd.Flags().StringVarP(&options.Output, "output", "o", "", "Scene Store V2 SQLite output")
	cmd.Flags().Int64Var(&fromTick, "from-tick", 0, "First server tick to include")
	cmd.Flags().Int64Var(&toTick, "to-tick", 0, "Last server tick to include")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace an existing valid Scene Store V2")
	cmd.Flags().BoolVar(&prepareOnly, "prepare-only", false, "Write the private extractor job without launching it")
	for _, name := range []string{"metadata", "events", "replay", "output"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func runPrepare(cmd *cobra.Command, options scenes.Options) func(do.Injector) error {
	return func(injector do.Injector) error {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return err
		}
		extractor, err := do.Invoke[*scenes.Extractor](injector)
		if err != nil {
			return err
		}
		return filelock.With(filepath.Join(config.Paths.Runtime, "operation.lock"), "scene_prepare", func() error {
			job, err := extractor.Prepare(options)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Prepared scene extraction job %s\n", job.Manifest)
			return err
		})
	}
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
		if _, err := fmt.Fprintf(cmd.OutOrStdout(), "Prepared scene extraction job %s\n", job.Manifest); err != nil {
			return err
		}
		if _, err := extractor.Launch(cmd.Context(), job); err != nil {
			return err
		}
		finalizer, err := do.Invoke[*scenes.Finalizer](injector)
		if err != nil {
			return err
		}
		info, err := finalizer.Finalize(cmd.Context(), scenes.Input{
			Result: job.Result, Stream: job.Stream, PlayerStates: job.PlayerStates,
		})
		if err != nil {
			return err
		}
		if err := extractor.Cleanup(job); err != nil {
			return err
		}
		_, err = fmt.Fprintf(cmd.OutOrStdout(), "Extracted %d frames to %s\n", info.FrameCount, info.Path)
		return err
	}
}
