//go:build bundled_artifacts

package bundled

import _ "embed"

//go:embed assets/mc-recorder-renderer.jar
var rendererMod []byte

//go:embed assets/mc-recorder-scene-extractor.zip
var sceneExtractor []byte

var artifacts = []Artifact{
	{Name: "mc-recorder-renderer.jar", Data: rendererMod},
	{Name: "mc-recorder-scene-extractor.zip", Data: sceneExtractor},
}
