package locate

import (
	"encoding/json"

	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/plays"
	"github.com/spf13/cobra"
)

func NewCommand() *cobra.Command {
	var playPath string
	var unixMS int64
	cmd := &cobra.Command{
		Use:     "locate",
		Short:   "Bracket a same-host timestamp with Server ticks from a completed Play",
		Example: "  recorder-minecraft plays locate --play PLAY --unix-ms 1794479466128",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			service := plays.New(&captures.Service{})
			result, err := service.LocateTime(cmd.Context(), playPath, unixMS)
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(result)
		},
	}
	cmd.Flags().StringVar(&playPath, "play", "", "Path to a completed Play directory")
	cmd.Flags().Int64Var(&unixMS, "unix-ms", 0, "Unix timestamp in milliseconds from the Recorder host")
	_ = cmd.MarkFlagRequired("play")
	_ = cmd.MarkFlagRequired("unix-ms")
	return cmd
}
