package align

import (
	"fmt"
	"path/filepath"
	"time"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models"
	"github.com/proj-airi/recorder-minecraft/internal/models/alignments"
	"github.com/proj-airi/recorder-minecraft/pkg/filelock"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var options alignments.Options
	var plays []string
	cmd := &cobra.Command{
		Use:   "align",
		Short: "Align Plays with the world stream and write container observation divergences",
		Long: `Write a session alignment: one world session stream and the named Plays of
the same session_id on one server tick timeline.

The output indexes world container records, each actor's container views,
joins and leaves, and visibility changes from perception.jsonl, by reference
to the source records. For each actor and container it reports the intervals
in which the world contents differ from the contents the actor last observed,
with the perception sample at the start of each interval.

It records observations and differences only. It does not decide what an
actor knows; consumers interpret these facts.`,
		Example: `  recorder-minecraft session align \
    --world-metadata WORLD/metadata.json \
    --world-events WORLD/world-events.jsonl \
    --play metadata=ALICE/metadata.json,events=ALICE/capture/events.jsonl,perception=ALICE/perception.jsonl \
    --play metadata=BOB/metadata.json,events=BOB/capture/events.jsonl \
    --output OUT/session-alignment.jsonl`,
		Args: cobra.NoArgs,
		PreRunE: func(_ *cobra.Command, _ []string) error {
			for _, spec := range plays {
				play, err := alignments.ParsePlay(spec)
				if err != nil {
					return err
				}
				options.Plays = append(options.Plays, play)
			}
			return nil
		},
		RunE: func(cmd *cobra.Command, _ []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			return command.Run(cmd.Context(), run(cmd, &options), configs.Package(configPath), models.Package)
		},
	}
	cmd.Flags().StringVar(&options.WorldMetadata, "world-metadata", "", "Closed WorldSessionMetadata ProtoJSON input")
	cmd.Flags().StringVar(&options.WorldEvents, "world-events", "", "WorldEvent ProtoJSON lines input")
	cmd.Flags().StringArrayVar(&plays, "play", nil, "One Play as metadata=PATH,events=PATH[,perception=PATH]; repeat for each actor")
	cmd.Flags().StringVarP(&options.Output, "output", "o", "", "SessionAlignmentRecord ProtoJSON lines output")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace an existing session alignment written by this processor")
	for _, name := range []string{"world-metadata", "world-events", "play", "output"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func run(cmd *cobra.Command, options *alignments.Options) func(do.Injector) error {
	return func(injector do.Injector) error {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return err
		}
		service, err := do.Invoke[*alignments.Service](injector)
		if err != nil {
			return err
		}
		return filelock.With(filepath.Join(config.Paths.Runtime, "operation.lock"), "session_align", func() error {
			started := time.Now()
			result, err := service.Align(cmd.Context(), *options)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(),
				"Aligned session %s with %d plays (%d without perception) to %s: %d indexed events and %d container divergences in %s\n",
				result.SessionID, result.Participants, result.WithoutVision, result.Output, result.Events, result.Divergences,
				time.Since(started).Round(time.Millisecond))
			return err
		})
	}
}
