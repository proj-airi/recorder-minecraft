package filelock

import (
	"errors"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestAcquireReportsHolderAndReleasesOnClose(t *testing.T) {
	t.Parallel()

	path := filepath.Join(t.TempDir(), "processor.lock")
	first, err := Acquire(path, "first-operation")
	require.NoError(t, err, "acquire first lock")
	t.Cleanup(func() { require.NoError(t, first.Close()) })

	second, err := Acquire(path, "second-operation")
	require.Error(t, err, "contending acquisition fails without blocking")
	assert.Nil(t, second)
	assert.Contains(t, err.Error(), "first-operation", "contention identifies the current holder")

	require.NoError(t, first.Close(), "release first lock")
	third, err := Acquire(path, "third-operation")
	require.NoError(t, err, "acquire released lock")
	require.NoError(t, third.Close(), "release third lock")
}

func TestWithReleasesLockAfterCallbackError(t *testing.T) {
	t.Parallel()

	path := filepath.Join(t.TempDir(), "processor.lock")
	want := errors.New("processing failed")
	err := With(path, "failing-operation", func() error { return want })
	require.Error(t, err)
	assert.ErrorIs(t, err, want)

	lock, err := Acquire(path, "next-operation")
	require.NoError(t, err, "callback error does not retain lock")
	require.NoError(t, lock.Close())
}
