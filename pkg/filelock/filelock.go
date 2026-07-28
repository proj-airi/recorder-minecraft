// Package filelock provides non-blocking advisory locks backed by open files.
package filelock

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

// Lock owns an advisory lock until Close is called.
type Lock struct {
	file *os.File
}

type owner struct {
	Holder    string `json:"holder"`
	PID       int    `json:"pid"`
	StartedAt string `json:"started_at"`
}

// Acquire takes a non-blocking exclusive lock on path. The file remains on
// disk after Close; the open file descriptor, rather than file existence, owns
// the lock. Its contents identify the holder in contention errors.
func Acquire(path, holder string) (*Lock, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, fmt.Errorf("create lock directory: %w", err)
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, fmt.Errorf("open lock file: %w", err)
	}
	if err := unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		detail := readOwner(file)
		_ = file.Close()
		return nil, fmt.Errorf("lock is already held%s", detail)
	}
	metadata, err := json.Marshal(owner{
		Holder: holder, PID: os.Getpid(), StartedAt: time.Now().UTC().Format(time.RFC3339Nano),
	})
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := file.Truncate(0); err != nil {
		_ = file.Close()
		return nil, err
	}
	if _, err := file.WriteAt(append(metadata, '\n'), 0); err != nil {
		_ = file.Close()
		return nil, err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return nil, err
	}
	return &Lock{file: file}, nil
}

func readOwner(file *os.File) string {
	metadata := make([]byte, 2048)
	count, _ := file.ReadAt(metadata, 0)
	if count == 0 {
		return ""
	}
	return " (" + string(metadata[:count]) + ")"
}

// Close releases the lock and closes its file descriptor. It is idempotent.
func (lock *Lock) Close() error {
	if lock == nil || lock.file == nil {
		return nil
	}
	file := lock.file
	lock.file = nil
	if err := unix.Flock(int(file.Fd()), unix.LOCK_UN); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

// With holds the lock for the duration of run.
func With(path, holder string, run func() error) error {
	lock, err := Acquire(path, holder)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Close() }()
	return run()
}
