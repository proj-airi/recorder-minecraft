package list

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/google/uuid"
	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	catalogv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/catalog/v1"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/spf13/cobra"
)

type options struct {
	ServerInstance   string
	Player           string
	StartedAtOrAfter string
	StartedBefore    string
	Output           string
}

type jsonPlay struct {
	ConnectionID     string       `json:"connection_id"`
	ServerName       string       `json:"server_name"`
	ServerInstanceID string       `json:"server_instance_id"`
	PlayerName       string       `json:"player_name"`
	PlayerUUID       string       `json:"player_uuid"`
	StartedAt        string       `json:"started_at"`
	EndedAt          string       `json:"ended_at,omitempty"`
	StartServerTick  int64        `json:"start_server_tick"`
	EndServerTick    *int64       `json:"end_server_tick,omitempty"`
	TerminalReason   *string      `json:"terminal_reason,omitempty"`
	CaptureFailure   *string      `json:"capture_failure,omitempty"`
	ValidationError  *string      `json:"validation_error,omitempty"`
	Summary          *jsonSummary `json:"summary,omitempty"`
}

type jsonSummary struct {
	DurationTicks              uint64              `json:"duration_ticks"`
	ObservedPathDistanceBlocks float64             `json:"observed_path_distance_blocks"`
	IdlePercentage             float64             `json:"idle_percentage"`
	PlayerStateCount           uint64              `json:"player_state_count"`
	FinalInventory             []jsonInventoryItem `json:"final_inventory"`
}

type jsonInventoryItem struct {
	Slot      int32  `json:"slot"`
	ItemID    string `json:"item_id"`
	Count     int32  `json:"count"`
	Damage    int32  `json:"damage"`
	MaxDamage int32  `json:"max_damage"`
}

func NewCommand() *cobra.Command {
	options := options{Output: "table"}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List Plays with transient summaries",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			filter, err := options.filter()
			if err != nil {
				return err
			}
			if options.Output != "table" && options.Output != "json" {
				return fmt.Errorf("--output must be table or json, got %q", options.Output)
			}
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			config, err := configs.Load(configPath)
			if err != nil {
				return err
			}
			service, err := catalog.New(config.Paths.Artifacts)
			if err != nil {
				return err
			}
			servers, err := service.SnapshotWithSummaries(cmd.Context(), filter)
			if err != nil {
				return fmt.Errorf("list Plays: %w", err)
			}
			replays := flatten(servers)
			if options.Output == "json" {
				return writeJSON(cmd.OutOrStdout(), replays)
			}
			return writeTable(cmd.OutOrStdout(), replays)
		},
	}
	cmd.Flags().StringVar(&options.ServerInstance, "server-instance", "", "Restrict Plays to one recorder installation UUID")
	cmd.Flags().StringVar(&options.Player, "player", "", "Restrict Plays to one Minecraft player UUID")
	cmd.Flags().StringVar(&options.StartedAtOrAfter, "started-at-or-after", "", "Include Plays started at or after this RFC 3339 timestamp")
	cmd.Flags().StringVar(&options.StartedBefore, "started-before", "", "Include Plays started before this RFC 3339 timestamp")
	cmd.Flags().StringVarP(&options.Output, "output", "o", options.Output, "Output format: table or json")
	return cmd
}

func (options options) filter() (catalog.Filter, error) {
	if err := validateUUID("--server-instance", options.ServerInstance); err != nil {
		return catalog.Filter{}, err
	}
	if err := validateUUID("--player", options.Player); err != nil {
		return catalog.Filter{}, err
	}
	filter := catalog.Filter{ServerInstanceID: options.ServerInstance, PlayerUUID: options.Player}
	var err error
	if options.StartedAtOrAfter != "" {
		filter.StartedAtOrAfter, err = parseTime("--started-at-or-after", options.StartedAtOrAfter)
		if err != nil {
			return catalog.Filter{}, err
		}
	}
	if options.StartedBefore != "" {
		filter.StartedBefore, err = parseTime("--started-before", options.StartedBefore)
		if err != nil {
			return catalog.Filter{}, err
		}
	}
	return filter, nil
}

func validateUUID(flag, value string) error {
	if value == "" {
		return nil
	}
	parsed, err := uuid.Parse(value)
	if err != nil || parsed.String() != value {
		return fmt.Errorf("%s must be a canonical UUID", flag)
	}
	return nil
}

func parseTime(flag, value string) (time.Time, error) {
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return time.Time{}, fmt.Errorf("%s must be an RFC 3339 timestamp: %w", flag, err)
	}
	return parsed, nil
}

func flatten(servers []*apiv1.ServerInstance) []*apiv1.Replay {
	replays := make([]*apiv1.Replay, 0)
	for _, server := range servers {
		for _, player := range server.GetPlayers() {
			replays = append(replays, player.GetReplays()...)
		}
	}
	sort.SliceStable(replays, func(left, right int) bool {
		return replays[left].GetStartedAt().AsTime().After(replays[right].GetStartedAt().AsTime())
	})
	return replays
}

func writeTable(output io.Writer, replays []*apiv1.Replay) error {
	writer := tabwriter.NewWriter(output, 0, 4, 2, ' ', 0)
	if _, err := fmt.Fprintln(writer, "STARTED (UTC)\tPLAYER\tSERVER\tCONNECTION\tDURATION\tDISTANCE\tIDLE\tFINAL INVENTORY"); err != nil {
		return err
	}
	for _, replay := range replays {
		duration, distance, idle, inventory := "-", "-", "-", "-"
		if summary := replay.GetSummary(); summary != nil {
			duration = formatDuration(summary.GetDurationTicks())
			distance = fmt.Sprintf("%.1f blocks", summary.GetObservedPathDistanceBlocks())
			idle = fmt.Sprintf("%.1f%%", summary.GetIdlePercentage())
			inventory = compactInventory(summary.GetFinalInventory())
		}
		if _, err := fmt.Fprintf(
			writer, "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
			replay.GetStartedAt().AsTime().UTC().Format(time.RFC3339Nano), replay.GetPlayerName(), replay.GetServerName(),
			replay.GetConnectionId(), duration, distance, idle, inventory,
		); err != nil {
			return err
		}
	}
	return writer.Flush()
}

func writeJSON(output io.Writer, replays []*apiv1.Replay) error {
	items := make([]jsonPlay, 0, len(replays))
	for _, replay := range replays {
		item := jsonPlay{
			ConnectionID: replay.GetConnectionId(), ServerName: replay.GetServerName(), ServerInstanceID: replay.GetServerInstanceId(),
			PlayerName: replay.GetPlayerName(), PlayerUUID: replay.GetPlayerUuid(), StartedAt: replay.GetStartedAt().AsTime().UTC().Format(time.RFC3339Nano),
			StartServerTick: replay.GetStartServerTick(), EndServerTick: replay.EndServerTick, TerminalReason: replay.TerminalReason,
			CaptureFailure: replay.CaptureFailure, ValidationError: replay.ValidationError,
		}
		if replay.GetEndedAt() != nil {
			item.EndedAt = replay.GetEndedAt().AsTime().UTC().Format(time.RFC3339Nano)
		}
		if summary := replay.GetSummary(); summary != nil {
			item.Summary = &jsonSummary{
				DurationTicks: summary.GetDurationTicks(), ObservedPathDistanceBlocks: summary.GetObservedPathDistanceBlocks(),
				IdlePercentage: summary.GetIdlePercentage(), PlayerStateCount: summary.GetPlayerStateCount(),
				FinalInventory: make([]jsonInventoryItem, 0, len(summary.GetFinalInventory())),
			}
			for _, inventoryItem := range summary.GetFinalInventory() {
				item.Summary.FinalInventory = append(item.Summary.FinalInventory, jsonInventoryItem{
					Slot: inventoryItem.GetSlot(), ItemID: inventoryItem.GetItemId(), Count: inventoryItem.GetCount(),
					Damage: inventoryItem.GetDamage(), MaxDamage: inventoryItem.GetMaxDamage(),
				})
			}
		}
		items = append(items, item)
	}
	encoder := json.NewEncoder(output)
	encoder.SetIndent("", "  ")
	return encoder.Encode(items)
}

func formatDuration(ticks uint64) string {
	seconds := ticks / 20
	return fmt.Sprintf("%02d:%02d (%d ticks)", seconds/60, seconds%60, ticks)
}

func compactInventory(items []*catalogv1.FinalInventoryItem) string {
	if len(items) == 0 {
		return "empty"
	}
	values := make([]string, 0, len(items))
	for _, item := range items {
		values = append(values, fmt.Sprintf("%d:%s x%d", item.GetSlot(), item.GetItemId(), item.GetCount()))
	}
	return strings.Join(values, ", ")
}
