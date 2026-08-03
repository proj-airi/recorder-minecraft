package renders

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestComposeVideoPublishesFFmpegOutput(t *testing.T) {
	t.Parallel()

	directory := t.TempDir()
	frames := filepath.Join(directory, "fpv_frames")
	require.NoError(t, os.Mkdir(frames, 0o750))
	executable := filepath.Join(directory, "fake-ffmpeg")
	require.NoError(t, os.WriteFile(executable, []byte("#!/bin/sh\nfor output do :; done\nprintf video > \"$output\"\n"), 0o700))
	job := Job{Directory: directory, Spec: &artifactsv1.RenderJob{OutputPath: frames}}
	result := &artifactsv1.RenderResult{
		Status:     artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE,
		FrameCount: 2, Width: 640, Height: 360, FramesPerSecond: 20,
	}

	output, err := (&Service{}).ComposeVideo(context.Background(), job, result, executable)
	require.NoError(t, err)
	raw, err := os.ReadFile(output)
	require.NoError(t, err)

	assert.Equal(t, filepath.Join(directory, "fpv.mp4"), output)
	assert.Equal(t, []byte("video"), raw)
}
