package plays

import (
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
)

func TestLocateTimeBracketsPlayerStates(t *testing.T) {
	// The packet timestamp can disagree with its neighbors. Only the
	// authoritative Player state samples constrain the result.
	events := []*artifactsv1.CaptureEvent{
		timedState(10, 1, 1000),
		packetApply(11, 2, "camera_or_position"),
		timedState(11, 3, 1050),
		timedState(12, 4, 1050),
		timedState(13, 5, 1100),
	}
	events[1].Identity.RecordedAtUnixMs = 3000
	play := writePlay(t, 10, 13, events)
	service := New(&captures.Service{})

	for _, test := range []struct {
		name  string
		query int64
		first *TimeSample
		last  *TimeSample
	}{
		{"before first sample", 999, nil, nil},
		{"first sample", 1000, &TimeSample{10, 1000}, &TimeSample{10, 1000}},
		{"between samples", 1025, &TimeSample{10, 1000}, &TimeSample{11, 1050}},
		{"same millisecond", 1050, &TimeSample{11, 1050}, &TimeSample{12, 1050}},
		{"after same millisecond", 1075, &TimeSample{12, 1050}, &TimeSample{13, 1100}},
		{"last sample", 1100, &TimeSample{13, 1100}, &TimeSample{13, 1100}},
		{"after last sample", 1101, nil, nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := service.LocateTime(t.Context(), play, test.query)
			require.NoError(t, err)
			require.Equal(t, testPlayerID, got.PlayerUUID)
			require.Equal(t, testConnectionID, got.ConnectionID)
			require.Equal(t, "session", got.SessionID)
			require.Equal(t, test.query, got.UnixMS)
			require.Equal(t, test.first, got.FirstSample)
			require.Equal(t, test.last, got.LastSample)
			if test.first == nil {
				require.Equal(t, "outside_capture", got.Status)
			} else {
				require.Equal(t, "bracketed", got.Status)
			}
		})
	}
}

func TestLocateTimeRejectsUnreliableSamples(t *testing.T) {
	service := New(&captures.Service{})
	for _, test := range []struct {
		name   string
		events []*artifactsv1.CaptureEvent
		error  string
	}{
		{"no samples", []*artifactsv1.CaptureEvent{packetApply(11, 1, "camera_or_position")}, "no player_state"},
		{"missing timestamp", []*artifactsv1.CaptureEvent{timedState(10, 1, 0)}, "recorded_at_unix_ms"},
		{"clock moved backwards after query", []*artifactsv1.CaptureEvent{
			timedState(10, 1, 1000), timedState(11, 2, 1100), timedState(12, 3, 1099),
		}, "out of order"},
		{"duplicate tick", []*artifactsv1.CaptureEvent{
			timedState(10, 1, 1000), timedState(10, 2, 1005),
		}, "out of order"},
	} {
		t.Run(test.name, func(t *testing.T) {
			play := writePlay(t, 10, 13, test.events)
			_, err := service.LocateTime(t.Context(), play, 1050)
			require.ErrorContains(t, err, test.error)
		})
	}
}

func TestLocateTimeRejectsFailedCapture(t *testing.T) {
	play := writePlay(t, 10, 13, []*artifactsv1.CaptureEvent{timedState(10, 1, 1000)})
	path := filepath.Join(play, "metadata.json")
	metadata := &artifactsv1.ServerMetadata{}
	raw, err := os.ReadFile(path)
	require.NoError(t, err)
	require.NoError(t, protojson.Unmarshal(raw, metadata))
	failure := "replay did not finalize"
	metadata.Connection.CaptureFailure = &failure
	writeProtoJSON(t, path, metadata, false)
	_, err = New(&captures.Service{}).LocateTime(t.Context(), play, 1000)
	require.ErrorContains(t, err, failure)
}

func timedState(tick int64, sequence uint64, unixMS int64) *artifactsv1.CaptureEvent {
	event := playerState(tick, sequence, "minecraft:overworld", 0, 64, 0, nil)
	event.Identity.RecordedAtUnixMs = unixMS
	return event
}
