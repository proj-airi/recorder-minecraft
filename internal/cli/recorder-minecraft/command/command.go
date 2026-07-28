package command

import (
	"context"
	"errors"

	"github.com/samber/do/v2"
	"github.com/spf13/cobra"
)

const (
	WorkspaceGroup  = "workspace"
	ProcessingGroup = "processing"
	OtherGroup      = "other"
)

// Register attaches command constructors to their direct parent.
func Register(parent *cobra.Command, constructors ...func() *cobra.Command) {
	for _, constructor := range constructors {
		parent.AddCommand(constructor())
	}
}

// ConfigPath resolves the root persistent flag at execution time.
func ConfigPath(command *cobra.Command) (string, error) {
	return command.Root().PersistentFlags().GetString("config")
}

// Run creates a command-scoped lazy injector and always shuts down the services
// that the command actually instantiated.
func Run(_ context.Context, invoke func(do.Injector) error, packages ...func(do.Injector)) (err error) {
	injector := do.New(packages...)
	defer func() {
		report := injector.Shutdown()
		if !report.Succeed {
			err = errors.Join(err, errors.New(report.Error()))
		}
	}()
	if invoke == nil {
		return nil
	}
	return invoke(injector)
}
