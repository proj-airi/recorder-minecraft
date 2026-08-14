package apis_test

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	catalogv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/catalog/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGeneratedPlaySummaryContracts(t *testing.T) {
	t.Parallel()

	requestFields := (&apiv1.ListReplaysRequest{}).ProtoReflect().Descriptor().Fields()
	assert.Equal(t, "includeSummary", requestFields.ByName("include_summary").JSONName())
	replayFields := (&apiv1.Replay{}).ProtoReflect().Descriptor().Fields()
	assert.Equal(t, "summary", replayFields.ByName("summary").JSONName())
	assert.Equal(t, "extensions", replayFields.ByName("extensions").JSONName())
	summaryFields := (&catalogv1.PlaySummary{}).ProtoReflect().Descriptor().Fields()
	assert.Equal(t, "durationTicks", summaryFields.ByName("duration_ticks").JSONName())
	assert.Equal(t, "observedPathDistanceBlocks", summaryFields.ByName("observed_path_distance_blocks").JSONName())
	assert.Equal(t, "idlePercentage", summaryFields.ByName("idle_percentage").JSONName())
	assert.Equal(t, "playerStateCount", summaryFields.ByName("player_state_count").JSONName())
	assert.Equal(t, "finalInventory", summaryFields.ByName("final_inventory").JSONName())

	_, source, _, ok := runtime.Caller(0)
	require.True(t, ok)
	root := filepath.Dir(source)
	openAPIV2, err := os.ReadFile(filepath.Join(root, "openapi", "v2", "recorder-minecraft.swagger.json"))
	require.NoError(t, err)
	openAPIV3, err := os.ReadFile(filepath.Join(root, "openapi", "v3", "recorder-minecraft.openapi.yaml"))
	require.NoError(t, err)
	for _, document := range []string{string(openAPIV2), string(openAPIV3)} {
		assert.Contains(t, document, "includeSummary")
		assert.Contains(t, document, "Calculate a transient summary for each completed Play after applying the filters.")
		assert.Contains(t, document, "recorder_minecraft.catalog.v1.PlaySummary")
		assert.Contains(t, document, "recorder_minecraft.api.v1.PlayExtension")
		assert.Contains(t, document, "Sum in blocks of three-dimensional displacement between successive same-dimension Player states.")
	}

	for _, generated := range []string{
		"FinalInventoryItem.java", "FinalInventoryItemKt.kt", "PlaySummary.java", "PlaySummaryKt.kt",
	} {
		_, err := os.Stat(filepath.Join(root, "sdk", "jvm", "dev", "recorderminecraft", "catalog", "v1", generated))
		require.NoError(t, err, "missing generated JVM contract %s", generated)
	}
	for _, generated := range []string{
		"PlayExtensionAsset.java", "PlayExtensionIdentity.java", "PlayExtensionManifest.java", "PlayExtensionTimeDomain.java",
	} {
		_, err := os.Stat(filepath.Join(root, "sdk", "jvm", "dev", "recorderminecraft", "artifacts", "v1", generated))
		require.NoError(t, err, "missing generated JVM extension contract %s", generated)
	}
}
