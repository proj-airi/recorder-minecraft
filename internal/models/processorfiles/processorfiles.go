// Package processorfiles holds the file integrity rules shared by JSONL
// processors: read-only inputs identified by digest, and outputs published
// atomically and replaced only when the processor owns them.
package processorfiles

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
)

// Input is a read-only input with its identity captured before use.
type Input struct {
	Path   string
	SHA256 string
	Info   os.FileInfo
}

// Digest hashes a regular, non-symlinked input and records its identity so
// Unchanged can later detect a rewrite during processing.
func Digest(path string) (Input, error) {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return Input{}, fmt.Errorf("resolve input %s: %w", path, err)
	}
	info, err := os.Lstat(resolved)
	if err != nil {
		return Input{}, fmt.Errorf("inspect input: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return Input{}, fmt.Errorf("input must be a non-symlinked regular file: %s", resolved)
	}
	file, err := os.Open(resolved)
	if err != nil {
		return Input{}, err
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return Input{}, fmt.Errorf("hash input %s: %w", resolved, err)
	}
	value := Input{Path: resolved, SHA256: hex.EncodeToString(hash.Sum(nil)), Info: info}
	if err := value.Unchanged(); err != nil {
		return Input{}, err
	}
	return value, nil
}

func (file Input) Unchanged() error {
	after, err := os.Stat(file.Path)
	if err != nil || !os.SameFile(file.Info, after) || after.Size() != file.Info.Size() || !after.ModTime().Equal(file.Info.ModTime()) {
		return fmt.Errorf("input changed while it was being processed: %s", file.Path)
	}
	return nil
}

// Lineage describes the input for an output header.
func (file Input) Lineage(role, mediaType, outputDirectory string) *artifactsv1.PerceptionInput {
	return &artifactsv1.PerceptionInput{Role: role, File: &artifactsv1.ArtifactFile{
		Path: RelativeInput(file.Path, outputDirectory), Sha256: file.SHA256, SizeBytes: uint64(file.Info.Size()), MediaType: mediaType,
	}}
}

// RelativeInput names an input relative to the output directory when it is
// inside it (the conventional play layout) and absolutely otherwise.
func RelativeInput(path, outputDirectory string) string {
	relative, err := filepath.Rel(outputDirectory, path)
	if err != nil || !filepath.IsLocal(relative) {
		return filepath.ToSlash(path)
	}
	return filepath.ToSlash(relative)
}

// PrepareOutput resolves the destination and refuses to replace anything the
// processor does not own. owned receives the absolute path of an existing
// regular file.
func PrepareOutput(label, path string, overwrite bool, owned func(string) bool) (string, error) {
	if path == "" {
		return "", fmt.Errorf("%s output path is required", label)
	}
	destination, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve %s output: %w", label, err)
	}
	if info, err := os.Lstat(destination); err == nil {
		if !overwrite {
			return "", fmt.Errorf("%s output exists: %s; pass --overwrite to replace it", label, destination)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return "", fmt.Errorf("%s output is not a replaceable regular file: %s", label, destination)
		}
		if !owned(destination) {
			return "", fmt.Errorf("refusing to replace a file this processor did not write: %s", destination)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("inspect %s output: %w", label, err)
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
		return "", fmt.Errorf("create %s output directory: %w", label, err)
	}
	return destination, nil
}

// FirstLine returns the first LF-terminated line of a file without its LF,
// for ownership checks against an output header.
func FirstLine(path string) ([]byte, bool) {
	file, err := os.Open(path)
	if err != nil {
		return nil, false
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReaderSize(file, 1024*1024)
	line, err := reader.ReadSlice('\n')
	if err != nil || len(line) < 2 {
		return nil, false
	}
	return line[:len(line)-1], true
}

// Publish stages the output beside its destination, then exposes it in one
// rename. Without overwrite a hard link publishes it, which fails instead of
// replacing a file that appeared after the ownership check.
func Publish(label, destination string, overwrite bool, write func(io.Writer) error) error {
	temporary, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".*.inprogress")
	if err != nil {
		return fmt.Errorf("create %s staging file: %w", label, err)
	}
	staging := temporary.Name()
	defer func() { _ = os.Remove(staging) }()
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return err
	}
	buffered := bufio.NewWriterSize(temporary, 1024*1024)
	if err := write(buffered); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := buffered.Flush(); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("flush %s output: %w", label, err)
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("sync %s output: %w", label, err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close %s output: %w", label, err)
	}
	if overwrite {
		if err := os.Rename(staging, destination); err != nil {
			return fmt.Errorf("publish %s output: %w", label, err)
		}
	} else if err := os.Link(staging, destination); err != nil {
		return fmt.Errorf("publish %s output: %w", label, err)
	}
	directory, err := os.Open(filepath.Dir(destination))
	if err != nil {
		return err
	}
	syncErr := directory.Sync()
	closeErr := directory.Close()
	if syncErr != nil {
		return syncErr
	}
	return closeErr
}
