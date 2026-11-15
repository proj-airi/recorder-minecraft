package plays

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"

	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
)

type TimeSample struct {
	ServerTick       int64 `json:"server_tick"`
	RecordedAtUnixMS int64 `json:"recorded_at_unix_ms"`
}

type TimeLocation struct {
	SessionID    string      `json:"session_id"`
	PlayerUUID   string      `json:"player_uuid"`
	ConnectionID string      `json:"connection_id"`
	UnixMS       int64       `json:"unix_ms"`
	Status       string      `json:"status"`
	FirstSample  *TimeSample `json:"first_sample,omitempty"`
	LastSample   *TimeSample `json:"last_sample,omitempty"`
}

// LocateTime brackets a same-host timestamp with authoritative Player state samples.
func (service *Service) LocateTime(ctx context.Context, playPath string, unixMS int64) (TimeLocation, error) {
	if err := ctx.Err(); err != nil {
		return TimeLocation{}, err
	}
	if unixMS <= 0 {
		return TimeLocation{}, errors.New("timestamp must be positive and given in Unix milliseconds")
	}
	metadata, err := service.captures.LoadMetadata(filepath.Join(playPath, "metadata.json"))
	if err != nil {
		return TimeLocation{}, err
	}
	if metadata.CaptureFailure != "" {
		return TimeLocation{}, fmt.Errorf("capture failed: %s", metadata.CaptureFailure)
	}

	var previous, first, last TimeSample
	_, err = service.captures.ScanEvents(filepath.Join(playPath, "capture", "events.jsonl"), metadata, func(event captures.Event) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if event.RecordType != "player_state" {
			return nil
		}
		if err := validatePlayerState(event.ServerTick, event.Message.GetPlayerState()); err != nil {
			return err
		}
		ms := event.Message.GetIdentity().GetRecordedAtUnixMs()
		if ms <= 0 {
			return fmt.Errorf("player_state at tick %d is missing a valid recorded_at_unix_ms", event.ServerTick)
		}
		if previous.RecordedAtUnixMS != 0 && (ms < previous.RecordedAtUnixMS || event.ServerTick <= previous.ServerTick) {
			return fmt.Errorf("player_state samples are out of order at tick %d", event.ServerTick)
		}
		sample := TimeSample{ServerTick: event.ServerTick, RecordedAtUnixMS: ms}
		previous = sample
		// NOTICE: Receipt bounds use the approach in
		// https://github.com/louisjia2008-ux/minecraft-companion/blob/6f9e3bc57eac604d532376a632bd9bc6fa76170b/tools/local-validation/align-recorder-play.py#L109-L120
		// Preserve every candidate tick when several samples share the queried millisecond.
		switch {
		case ms < unixMS:
			first = sample
		case ms == unixMS:
			if last.RecordedAtUnixMS != ms {
				first = sample
			}
			last = sample
		case last.RecordedAtUnixMS == 0:
			last = sample
		}
		return nil
	})
	if err != nil {
		return TimeLocation{}, err
	}
	if previous.RecordedAtUnixMS == 0 {
		return TimeLocation{}, errors.New("completed capture has no player_state records")
	}

	result := TimeLocation{
		SessionID: metadata.SessionID, PlayerUUID: metadata.PlayerUUID, ConnectionID: metadata.ConnectionID,
		UnixMS: unixMS, Status: "outside_capture",
	}
	if first.RecordedAtUnixMS != 0 && last.RecordedAtUnixMS != 0 {
		result.Status = "bracketed"
		result.FirstSample, result.LastSample = &first, &last
	}
	return result, nil
}
