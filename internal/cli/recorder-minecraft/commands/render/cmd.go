package render

import (
	"fmt"
	"path/filepath"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models"
	renderjob "github.com/proj-airi/recorder-minecraft/internal/models/renders"
	"github.com/proj-airi/recorder-minecraft/pkg/filelock"
	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	options := renderjob.Options{Width: 640, Height: 360, FPS: 20}
	var fromTick int64
	var toTick int64
	var offline bool
	var framesOnly bool
	var ffmpeg string
	cmd := &cobra.Command{
		Use:     "render",
		Short:   "Render a Flashback replay into first-person RGB frames",
		GroupID: command.ProcessingGroup,
		Long: `Render one completed connection using the client-only renderer mod.

The command validates capture and replay identity, prepares render-job.json,
then launches the renderer unless --prepare-only is set.`,
		Example: `  recorder-minecraft render \
    --metadata PLAY/metadata.json \
    --events PLAY/capture/events.jsonl \
    --replay PLAY/capture/replay.zip \
    --output PLAY/renders`,
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
			return command.Run(cmd.Context(), run(cmd, options, offline, framesOnly, ffmpeg), configs.Package(configPath), models.Package)
		},
	}
	cmd.Flags().StringVar(&options.Metadata, "metadata", "", "Completed ServerMetadata ProtoJSON input")
	cmd.Flags().StringVar(&options.Events, "events", "", "CaptureEvent ProtoJSON lines input")
	cmd.Flags().StringVar(&options.Replay, "replay", "", "Flashback replay ZIP input")
	cmd.Flags().StringVarP(&options.Output, "output", "o", "", "Render directory output")
	cmd.Flags().IntVar(&options.Width, "width", options.Width, "Frame width in pixels")
	cmd.Flags().IntVar(&options.Height, "height", options.Height, "Frame height in pixels")
	cmd.Flags().IntVar(&options.FPS, "fps", options.FPS, "Output frames per second")
	cmd.Flags().Int64Var(&fromTick, "from-tick", 0, "First server tick to include")
	cmd.Flags().Int64Var(&toTick, "to-tick", 0, "Last server tick to include")
	cmd.Flags().BoolVar(&options.NoGUI, "no-gui", false, "Omit the client HUD")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace a renderer-owned output directory")
	cmd.Flags().BoolVar(&options.PrepareOnly, "prepare-only", false, "Write the job without launching Minecraft")
	cmd.Flags().BoolVar(&offline, "offline", false, "Run Gradle without network access")
	cmd.Flags().BoolVar(&framesOnly, "frames-only", false, "Keep the verified PNG sequence without composing fpv.mp4")
	cmd.Flags().StringVar(&ffmpeg, "ffmpeg", "ffmpeg", "FFmpeg executable used to compose fpv.mp4")
	for _, name := range []string{"metadata", "events", "replay", "output"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func run(cmd *cobra.Command, options renderjob.Options, offline, framesOnly bool, ffmpeg string) func(do.Injector) error {
	return func(injector do.Injector) error {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return err
		}
		service, err := do.Invoke[*renderjob.Service](injector)
		if err != nil {
			return err
		}
		return filelock.With(filepath.Join(config.Paths.Runtime, "operation.lock"), "render", func() error {
			job, err := service.Prepare(options)
			if err != nil {
				return err
			}
			if _, err := fmt.Fprintf(cmd.OutOrStdout(), "Prepared render job %s\n", job.Manifest); err != nil {
				return err
			}
			if options.PrepareOnly {
				return nil
			}
			result, err := service.Launch(cmd.Context(), job, offline)
			if err != nil {
				return err
			}
			if !framesOnly && result.GetStatus() == artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE {
				video, err := service.ComposeVideo(cmd.Context(), job, result, ffmpeg)
				if err != nil {
					return err
				}
				if _, err := fmt.Fprintf(cmd.OutOrStdout(), "Composed video %s\n", video); err != nil {
					return err
				}
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Rendered ticks %d..%d to %s\n", result.GetGlobalTicks().GetFirstTick(), result.GetGlobalTicks().GetLastTick(), result.GetOutputPath())
			return err
		})
	}
}
