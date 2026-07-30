package bundled

import "errors"

var ErrUnavailable = errors.New("this recorder-minecraft binary was built without bundled artifacts")

type Artifact struct {
	Name string
	Data []byte
}

// Artifacts describes build-time payloads under stable export names.
func Artifacts() ([]Artifact, error) {
	if len(artifacts) == 0 {
		return nil, ErrUnavailable
	}

	result := make([]Artifact, len(artifacts))
	copy(result, artifacts)
	return result, nil
}
