package extract

import (
	"fmt"
	"path/filepath"
	"time"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models"
	"github.com/proj-airi/recorder-minecraft/internal/models/perceptions"
	"github.com/proj-airi/recorder-minecraft/pkg/filelock"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	options := perceptions.Options{
		IntervalTicks:      perceptions.DefaultIntervalTicks,
		VerticalFOVDegrees: perceptions.DefaultVerticalFOVDegrees,
		AspectRatio:        perceptions.DefaultAspectRatio,
		MaxDistanceBlocks:  perceptions.DefaultMaxDistanceBlocks,
	}
	var fromTick int64
	var toTick int64
	cmd := &cobra.Command{
		Use:   "extract",
		Short: "Write which entities and block entities the player could see",
		Long: `Write perception.jsonl: for sampled server ticks, the entities and block
entities inside the recorded player's view frustum that sight rays reach
without crossing an opaque full-cube block.

The result is reconstructed actor perception, not a capture. The header
records the processor version, input digests, and every assumption (FOV,
aspect, eye heights, occluder model, sampling, distance limit). Cells the
scene does not know make a target undetermined, never visible or hidden.`,
		Example: `  recorder-minecraft perception extract \
    --metadata PLAY/metadata.json \
    --events PLAY/capture/events.jsonl \
    --scene PLAY/scene.sqlite3 \
    --output PLAY/perception.jsonl`,
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
	cmd.Flags().StringVar(&options.Scene, "scene", "", "Scene Store V2 SQLite input")
	cmd.Flags().StringVarP(&options.Output, "output", "o", "", "PerceptionRecord ProtoJSON lines output")
	cmd.Flags().Int64Var(&fromTick, "from-tick", 0, "First server tick to sample")
	cmd.Flags().Int64Var(&toTick, "to-tick", 0, "Last server tick to sample")
	cmd.Flags().Int64Var(&options.IntervalTicks, "interval-ticks", options.IntervalTicks, "Server ticks between samples")
	cmd.Flags().Float64Var(&options.VerticalFOVDegrees, "fov", options.VerticalFOVDegrees, "Assumed vertical field of view in degrees (vanilla FOV option)")
	cmd.Flags().Float64Var(&options.AspectRatio, "aspect", options.AspectRatio, "Assumed window width divided by height")
	cmd.Flags().Float64Var(&options.MaxDistanceBlocks, "max-distance", options.MaxDistanceBlocks, "Maximum eye-to-target distance in blocks, further capped by view distance")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace an existing perception output written by this processor")
	for _, name := range []string{"metadata", "events", "scene", "output"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func run(cmd *cobra.Command, options perceptions.Options) func(do.Injector) error {
	return func(injector do.Injector) error {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return err
		}
		service, err := do.Invoke[*perceptions.Service](injector)
		if err != nil {
			return err
		}
		return filelock.With(filepath.Join(config.Paths.Runtime, "operation.lock"), "perception_extract", func() error {
			started := time.Now()
			result, err := service.Extract(cmd.Context(), options)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(),
				"Extracted %d perception samples for ticks %d..%d to %s (%d visible entity and %d visible block entity observations, %d undetermined; decoded %d section blobs for %d section versions) in %s\n",
				result.SampleCount, result.FirstTick, result.LastTick, result.Output, result.VisibleEntities, result.VisibleBlockEntities,
				result.UndeterminedTargets, result.DecodedSectionBlobs, result.SectionVersionsInWindow, time.Since(started).Round(time.Millisecond))
			return err
		})
	}
}
