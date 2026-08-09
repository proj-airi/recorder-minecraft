package plays

import (
	"context"
	"errors"
	"fmt"
	"math"
	"path/filepath"
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
)

type InventoryItem struct {
	Slot      int32
	ItemID    string
	Count     int32
	Damage    int32
	MaxDamage int32
}

type Summary struct {
	DurationTicks              int64
	ObservedPathDistanceBlocks float64
	IdlePercentage             float64
	PlayerStateCount           uint64
	FinalInventory             []InventoryItem
}

type Service struct {
	captures *captures.Service
}

func New(captureService *captures.Service) *Service {
	return &Service{captures: captureService}
}

// Summarize derives transient metrics from one completed Play's primitive capture inputs.
func (service *Service) Summarize(ctx context.Context, playPath string) (Summary, error) {
	if err := ctx.Err(); err != nil {
		return Summary{}, err
	}
	metadata, err := service.captures.LoadMetadata(filepath.Join(playPath, "metadata.json"))
	if err != nil {
		return Summary{}, err
	}
	// Capture bounds are inclusive, so a Play that starts and ends on one tick has
	// a duration of one tick.
	durationTicks := metadata.EndTick - metadata.StartTick + 1
	if durationTicks <= 0 {
		return Summary{}, errors.New("capture duration exceeds the supported tick range")
	}

	playerStates := make(map[int64]*artifactsv1.PlayerStateEvent)
	controlStates := make(map[int64]*artifactsv1.ControlState)
	activeTicks := make(map[int64]struct{})
	_, err = service.captures.ScanEvents(filepath.Join(playPath, "capture", "events.jsonl"), metadata, func(event captures.Event) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		switch event.RecordType {
		case "player_state":
			state := event.Message.GetPlayerState()
			if err := validatePlayerState(event.ServerTick, state); err != nil {
				return err
			}
			if _, exists := playerStates[event.ServerTick]; exists {
				return fmt.Errorf("duplicate player_state at tick %d", event.ServerTick)
			}
			playerStates[event.ServerTick] = state
		case "control_state":
			state := event.Message.GetControlState().GetState()
			if err := validateControlState(event.ServerTick, state); err != nil {
				return err
			}
			if _, exists := controlStates[event.ServerTick]; exists {
				return fmt.Errorf("duplicate control_state at tick %d", event.ServerTick)
			}
			controlStates[event.ServerTick] = state
		case "packet_apply":
			packet := event.Message.GetPacketApply().GetPacket()
			if err := validatePacket("packet_apply", event.ServerTick, packet); err != nil {
				return err
			}
			// Only packet classes that express player intent count as activity. Network
			// bookkeeping and passive protocol traffic must not make an AFK Play active.
			if semanticActivity(packet.GetActionKind()) {
				activeTicks[event.ServerTick] = struct{}{}
			}
		case "packet_arrival":
			if err := validatePacket("packet_arrival", event.ServerTick, event.Message.GetPacketArrival().GetPacket()); err != nil {
				return err
			}
		case "replay_timeline":
			timeline := event.Message.GetReplayTimeline()
			if timeline == nil || timeline.GetProtocol() == "" {
				return fmt.Errorf("replay_timeline at tick %d is incomplete", event.ServerTick)
			}
		}
		return nil
	})
	if err != nil {
		return Summary{}, err
	}
	if len(playerStates) == 0 {
		return Summary{}, errors.New("completed capture has no player_state records")
	}
	if len(controlStates) == 0 {
		return Summary{}, errors.New("completed capture has no control_state records")
	}

	ticks := sortedTicks(playerStates)
	if err := validateStateCoverage(ticks, controlStates); err != nil {
		return Summary{}, err
	}

	var distance float64
	for index, tick := range ticks {
		control := controlStates[tick]
		// Held movement and a non-zero camera delta are activity even if the world
		// state does not change, for example when the player walks into a wall.
		if movementHeld(control) || control.GetCameraDeltaYaw() != 0 || control.GetCameraDeltaPitch() != 0 {
			activeTicks[tick] = struct{}{}
		}
		// The first selected slot establishes the baseline. Only later changes are
		// player actions.
		if index > 0 && control.GetSelectedSlot() != controlStates[ticks[index-1]].GetSelectedSlot() {
			activeTicks[tick] = struct{}{}
		}
		if index == 0 {
			continue
		}
		previous := playerStates[ticks[index-1]]
		current := playerStates[tick]
		// Coordinates from different dimensions are not comparable. Same-dimension
		// edges include teleports because the metric is observed path distance.
		if previous.GetDimension() == current.GetDimension() {
			segment := displacement(previous.GetPosition(), current.GetPosition())
			if !finite(segment, distance+segment) {
				return Summary{}, fmt.Errorf("player_state edge ending at tick %d has a non-finite displacement", tick)
			}
			distance += segment
		}
	}

	finalState := playerStates[ticks[len(ticks)-1]]
	inventory := make([]InventoryItem, 0, len(finalState.GetInventory()))
	for _, item := range finalState.GetInventory() {
		if item == nil {
			return Summary{}, errors.New("final player_state contains an incomplete inventory slot")
		}
		inventory = append(inventory, InventoryItem{
			Slot: item.GetSlot(), ItemID: item.GetItemId(), Count: item.GetCount(),
			Damage: item.GetDamage(), MaxDamage: item.GetMaxDamage(),
		})
	}

	// The duration is authoritative. Missing leading or trailing state samples
	// therefore remain idle instead of shrinking the denominator.
	return Summary{
		DurationTicks:              durationTicks,
		ObservedPathDistanceBlocks: distance,
		IdlePercentage:             100 * float64(durationTicks-int64(len(activeTicks))) / float64(durationTicks),
		PlayerStateCount:           uint64(len(playerStates)),
		FinalInventory:             inventory,
	}, nil
}

func validatePlayerState(tick int64, state *artifactsv1.PlayerStateEvent) error {
	if state == nil || state.GetDimension() == "" || state.GetPosition() == nil || state.GetRotation() == nil ||
		state.GetVelocity() == nil || state.GetAbilities() == nil || state.GetReplayCoverage() == nil {
		return fmt.Errorf("player_state at tick %d is incomplete", tick)
	}
	position := state.GetPosition()
	if !finite(position.GetX(), position.GetY(), position.GetZ()) {
		return fmt.Errorf("player_state at tick %d has a non-finite position", tick)
	}
	rotation := state.GetRotation()
	velocity := state.GetVelocity()
	if !finite(
		rotation.GetYaw(), rotation.GetPitch(), rotation.GetHeadYaw(),
		velocity.GetX(), velocity.GetY(), velocity.GetZ(),
		state.GetHealth(), state.GetMaxHealth(), state.GetAbsorption(), state.GetSaturation(), state.GetExperienceProgress(),
	) {
		return fmt.Errorf("player_state at tick %d has a non-finite numeric value", tick)
	}
	for _, item := range state.GetInventory() {
		if item == nil || item.GetItemId() == "" {
			return fmt.Errorf("player_state at tick %d has an incomplete inventory slot", tick)
		}
	}
	for _, effect := range state.GetEffects() {
		if effect == nil {
			return fmt.Errorf("player_state at tick %d has an incomplete status effect", tick)
		}
	}
	for _, passenger := range state.GetPassengers() {
		if passenger == nil {
			return fmt.Errorf("player_state at tick %d has an incomplete passenger", tick)
		}
	}
	return nil
}

func validateControlState(tick int64, state *artifactsv1.ControlState) error {
	if state == nil {
		return fmt.Errorf("control_state at tick %d is incomplete", tick)
	}
	if !finite(state.GetCameraYaw(), state.GetCameraPitch(), state.GetCameraDeltaYaw(), state.GetCameraDeltaPitch()) {
		return fmt.Errorf("control_state at tick %d has a non-finite camera value", tick)
	}
	return nil
}

func validatePacket(recordType string, tick int64, packet *artifactsv1.Packet) error {
	if packet == nil || packet.GetIdentity() == nil || packet.GetActionKind() == "" {
		return fmt.Errorf("%s at tick %d is incomplete", recordType, tick)
	}
	if action := packet.GetCameraOrPosition(); action != nil {
		if !finiteOptional(action.X, action.Y, action.Z, action.Yaw, action.Pitch) {
			return fmt.Errorf("%s at tick %d has a non-finite camera or position value", recordType, tick)
		}
	}
	if position := packet.GetBlockPosition(); position != nil && !finiteVector(position) {
		return fmt.Errorf("%s at tick %d has a non-finite block position", recordType, tick)
	}
	if hit := packet.GetBlockHit(); hit != nil {
		if hit.GetBlockPosition() == nil || hit.GetLocation() == nil {
			return fmt.Errorf("%s at tick %d has an incomplete block hit", recordType, tick)
		}
		if !finiteVector(hit.GetBlockPosition()) || !finiteVector(hit.GetLocation()) {
			return fmt.Errorf("%s at tick %d has a non-finite block hit", recordType, tick)
		}
	}
	return nil
}

func finiteVector(vector *artifactsv1.Vector3) bool {
	return finite(vector.GetX(), vector.GetY(), vector.GetZ())
}

func finiteOptional(values ...*float64) bool {
	for _, value := range values {
		if value != nil && !finite(*value) {
			return false
		}
	}
	return true
}

func finite(values ...float64) bool {
	for _, value := range values {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return false
		}
	}
	return true
}

func sortedTicks(states map[int64]*artifactsv1.PlayerStateEvent) []int64 {
	ticks := make([]int64, 0, len(states))
	for tick := range states {
		ticks = append(ticks, tick)
	}
	sort.Slice(ticks, func(left, right int) bool { return ticks[left] < ticks[right] })
	return ticks
}

func validateStateCoverage(ticks []int64, controls map[int64]*artifactsv1.ControlState) error {
	if len(ticks) != len(controls) {
		return errors.New("player_state and control_state tick sets differ")
	}
	for index, tick := range ticks {
		if _, exists := controls[tick]; !exists {
			return errors.New("player_state and control_state tick sets differ")
		}
		if index > 0 && tick != ticks[index-1]+1 {
			return fmt.Errorf("state coverage has an internal gap after tick %d", ticks[index-1])
		}
	}
	return nil
}

func movementHeld(state *artifactsv1.ControlState) bool {
	return state.GetForward() || state.GetBackward() || state.GetLeft() || state.GetRight() ||
		state.GetJump() || state.GetSneak() || state.GetSprint()
}

func semanticActivity(actionKind string) bool {
	// These are the normalized packet-action categories that represent an
	// intentional player operation. Unknown categories remain passive.
	switch actionKind {
	case "player_action", "interact", "use", "swing", "inventory", "stance", "text_redacted":
		return true
	default:
		return false
	}
}

func displacement(left, right *artifactsv1.Vector3) float64 {
	dx := right.GetX() - left.GetX()
	dy := right.GetY() - left.GetY()
	dz := right.GetZ() - left.GetZ()
	return math.Sqrt(dx*dx + dy*dy + dz*dz)
}
