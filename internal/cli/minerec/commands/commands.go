package commands

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/actions"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/config"
	initialize "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/init"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/options"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/render"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/scene"
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/version"
	"github.com/spf13/cobra"
)

// Register attaches every top-level command and lets nested packages register
// their own children.
func Register(root *cobra.Command) {
	actions.Register(root)
	config.Register(root)
	scene.Register(root)
	command.Register(root, initialize.NewCommand, options.NewCommand, render.NewCommand, version.NewCommand)
}
