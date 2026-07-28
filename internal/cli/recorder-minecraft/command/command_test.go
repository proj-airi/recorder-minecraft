package command

import (
	"context"
	"testing"

	"github.com/samber/do/v2"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type lifecycleService struct {
	stopped *bool
}

func (service *lifecycleService) Shutdown() error {
	*service.stopped = true
	return nil
}

func TestRunInstantiatesProvidersLazily(t *testing.T) {
	t.Parallel()

	created := false
	packageProvider := func(injector do.Injector) {
		do.Provide(injector, func(do.Injector) (*lifecycleService, error) {
			created = true
			return &lifecycleService{}, nil
		})
	}

	require.NoError(t, Run(context.Background(), nil, packageProvider))
	assert.False(t, created, "unused provider remains uninstantiated")
}

func TestRunShutsDownInstantiatedServices(t *testing.T) {
	t.Parallel()

	stopped := false
	packageProvider := func(injector do.Injector) {
		do.Provide(injector, func(do.Injector) (*lifecycleService, error) {
			return &lifecycleService{stopped: &stopped}, nil
		})
	}

	err := Run(context.Background(), func(injector do.Injector) error {
		_, err := do.Invoke[*lifecycleService](injector)
		return err
	}, packageProvider)
	require.NoError(t, err)
	assert.True(t, stopped, "instantiated service is shut down")
}
