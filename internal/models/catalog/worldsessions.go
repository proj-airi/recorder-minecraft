package catalog

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	pathpkg "path"
	"path/filepath"
	"strings"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/worldcaptures"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	alignmentsDirectory = "alignments"
	alignmentSuffix     = ".jsonl"
	// maxAlignmentHeaderBytes bounds the first line read from an alignment file. The header lists
	// participants and input digests only, so it stays far below this size.
	maxAlignmentHeaderBytes = 4 * 1024 * 1024
)

// worldSessions reads world/sessions/ of one server instance. A session whose metadata cannot be
// validated stays visible with a validation error, like an invalid Play.
func (service *Service) worldSessions(serverPath, serverName, serverID string) []*apiv1.WorldSession {
	root := filepath.Join(serverPath, "world", "sessions")
	entries, err := readDirectory(root)
	if err != nil {
		return []*apiv1.WorldSession{}
	}
	sessions := make([]*apiv1.WorldSession, 0, len(entries))
	for _, entry := range entries {
		startedName, sessionID, ok := splitIdentity(entry.Name())
		if !ok {
			continue
		}
		startedAt, err := parseDirectoryTime(startedName)
		if err != nil {
			continue
		}
		sessionPath := filepath.Join(root, entry.Name())
		if !safeDirectory(sessionPath, entry) {
			continue
		}
		sessions = append(sessions, service.readWorldSession(sessionPath, entry.Name(), serverName, serverID, sessionID, startedAt))
	}
	return sessions
}

func (service *Service) readWorldSession(sessionPath, id, serverName, serverID, sessionID string, directoryStartedAt time.Time) *apiv1.WorldSession {
	session := &apiv1.WorldSession{
		Id: id, SessionId: sessionID, ServerName: serverName, ServerInstanceId: serverID,
		StartedAt:   timestamppb.New(directoryStartedAt),
		MetadataUrl: service.assetURL(sessionPath, "metadata.json"),
		EventsUrl:   service.assetURL(sessionPath, worldcaptures.EventsFile),
		KnownGaps:   []string{},
		Alignments:  service.alignments(sessionPath, sessionID),
		Plays:       []*apiv1.WorldSessionPlay{},
	}
	invalid := func(err error) *apiv1.WorldSession {
		message := publicSessionError(sessionPath, err)
		session.ValidationError = &message
		return session
	}

	metadata := &artifactsv1.WorldSessionMetadata{}
	if err := readProtoJSON(filepath.Join(sessionPath, "metadata.json"), metadata); err != nil {
		return invalid(err)
	}
	startedAt := metadata.GetStartedAt()
	if metadata.GetServer().GetName() != serverName || metadata.GetServer().GetInstanceId() != serverID ||
		metadata.GetSessionId() != sessionID || startedAt == nil ||
		!startedAt.AsTime().Truncate(time.Millisecond).Equal(directoryStartedAt) {
		return invalid(errors.New("world session metadata identity does not match its directory"))
	}
	session.StartedAt = startedAt
	session.EndedAt = metadata.GetEndedAt()
	session.StartServerTick = metadata.GetStartServerTick()
	session.EndServerTick = metadata.EndServerTick
	session.TerminalReason = metadata.TerminalReason
	session.StreamFailure = metadata.StreamFailure
	if gaps := metadata.GetKnownGaps(); gaps != nil {
		session.KnownGaps = gaps
	}
	// NOTICE: worldcaptures owns the canonical metadata rules (schema, scope, provenance, closed
	// stream). The catalog reuses it instead of duplicating them, and only adds the identity checks
	// that need the directory hierarchy.
	if _, err := service.worlds.LoadMetadata(filepath.Join(sessionPath, "metadata.json")); err != nil {
		return invalid(err)
	}
	if info, err := os.Lstat(filepath.Join(sessionPath, worldcaptures.EventsFile)); err != nil ||
		info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return invalid(fmt.Errorf("world events file %s is missing", worldcaptures.EventsFile))
	}
	return session
}

// alignments lists alignments/*.jsonl. Only each header line is read.
func (service *Service) alignments(sessionPath, sessionID string) []*apiv1.WorldSessionAlignment {
	root := filepath.Join(sessionPath, alignmentsDirectory)
	entries, err := readDirectory(root)
	if err != nil {
		return []*apiv1.WorldSessionAlignment{}
	}
	result := make([]*apiv1.WorldSessionAlignment, 0, len(entries))
	for _, entry := range entries {
		name, ok := strings.CutSuffix(entry.Name(), alignmentSuffix)
		if !ok || name == "" || strings.HasPrefix(name, ".") || entry.Type()&os.ModeSymlink != 0 || !entry.Type().IsRegular() {
			continue
		}
		path := filepath.Join(root, entry.Name())
		info, err := os.Lstat(path)
		if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			continue
		}
		alignment := &apiv1.WorldSessionAlignment{
			Name: name, Url: service.assetURL(sessionPath, pathpkg.Join(alignmentsDirectory, entry.Name())),
			SizeBytes: uint64(info.Size()), Participants: []*apiv1.AlignmentParticipant{},
		}
		if err := readAlignmentHeader(path, sessionID, alignment); err != nil {
			message := publicSessionError(sessionPath, err)
			alignment.ValidationError = &message
		}
		result = append(result, alignment)
	}
	return result
}

func readAlignmentHeader(path, sessionID string, alignment *apiv1.WorldSessionAlignment) error {
	file, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("read session alignment: %w", err)
	}
	defer func() { _ = file.Close() }()
	line, err := bufio.NewReaderSize(io.LimitReader(file, maxAlignmentHeaderBytes+1), 64*1024).ReadBytes('\n')
	if err != nil {
		if errors.Is(err, io.EOF) && len(line) > maxAlignmentHeaderBytes {
			return fmt.Errorf("session alignment header exceeds %d bytes", maxAlignmentHeaderBytes)
		}
		return errors.New("session alignment has no complete header line")
	}
	record := &artifactsv1.SessionAlignmentRecord{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(line[:len(line)-1], record); err != nil {
		return fmt.Errorf("session alignment header is not valid ProtoJSON: %w", err)
	}
	header := record.GetHeader()
	if record.GetSchemaVersion() != 1 || header == nil {
		return errors.New("session alignment does not start with a schema version 1 header")
	}
	if header.GetSessionId() != sessionID {
		return errors.New("session alignment header names another session")
	}
	alignment.EventCount = header.GetEventCount()
	alignment.DivergenceCount = header.GetDivergenceCount()
	for _, participant := range header.GetParticipants() {
		perception := false
		for _, input := range participant.GetInputs() {
			perception = perception || input.GetRole() == "perception"
		}
		alignment.Participants = append(alignment.Participants, &apiv1.AlignmentParticipant{
			PlayerUuid: participant.GetPlayerUuid(), PlayerName: participant.GetPlayerName(), ConnectionId: participant.GetConnectionId(),
			StartServerTick: participant.GetCoverage().GetFirstTick(), EndServerTick: participant.GetCoverage().GetLastTick(),
			PerceptionProvided: perception,
		})
	}
	return nil
}

// sessionIndex resolves Play metadata to the world sessions of the same server instance.
type sessionIndex struct {
	byID        map[string]*apiv1.WorldSession
	bySessionID map[string][]*apiv1.WorldSession
}

func newSessionIndex(sessions []*apiv1.WorldSession) sessionIndex {
	index := sessionIndex{byID: map[string]*apiv1.WorldSession{}, bySessionID: map[string][]*apiv1.WorldSession{}}
	for _, session := range sessions {
		index.byID[session.GetId()] = session
		index.bySessionID[session.GetSessionId()] = append(index.bySessionID[session.GetSessionId()], session)
	}
	return index
}

// resolve prefers the explicit world_container_truth reference. Without one, a Play joins the only
// world session with its session_id; an ambiguous session_id links nothing.
func (index sessionIndex) resolve(metadata *artifactsv1.ServerMetadata) (string, apiv1.WorldSessionLink) {
	if id, ok := worldSessionDirectory(metadata.GetWorldContainerTruth().GetMetadata()); ok {
		if session, found := index.byID[id]; found && session.GetSessionId() == metadata.GetSessionId() {
			return id, apiv1.WorldSessionLink_WORLD_SESSION_LINK_CONTAINER_TRUTH
		}
	}
	if candidates := index.bySessionID[metadata.GetSessionId()]; metadata.GetSessionId() != "" && len(candidates) == 1 {
		return candidates[0].GetId(), apiv1.WorldSessionLink_WORLD_SESSION_LINK_SESSION_ID
	}
	return "", apiv1.WorldSessionLink_WORLD_SESSION_LINK_UNSPECIFIED
}

// worldSessionDirectory extracts <dir> from the server-relative world/sessions/<dir>/metadata.json.
func worldSessionDirectory(reference string) (string, bool) {
	if reference == "" || pathpkg.Clean(reference) != reference {
		return "", false
	}
	parts := strings.Split(reference, "/")
	if len(parts) != 4 || parts[0] != "world" || parts[1] != "sessions" || parts[3] != "metadata.json" {
		return "", false
	}
	return parts[2], true
}

func linkPlays(sessions []*apiv1.WorldSession, players []*apiv1.Player) {
	index := newSessionIndex(sessions)
	for _, player := range players {
		for _, replay := range player.GetReplays() {
			session, ok := index.byID[replay.GetWorldSessionId()]
			if !ok {
				continue
			}
			session.Plays = append(session.Plays, &apiv1.WorldSessionPlay{
				PlayerUuid: player.GetUuid(), PlayerName: player.GetName(), ConnectionId: replay.GetConnectionId(), Link: replay.GetWorldSessionLink(),
			})
		}
	}
}

func publicSessionError(sessionPath string, err error) string {
	return strings.ReplaceAll(err.Error(), sessionPath, "world session")
}
