package commands

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/actions"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/config"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/exportassets"
	initialize "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/init"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/options"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/plays"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/render"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/scene"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/serve"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/version"
	"github.com/spf13/cobra"
)

// Register attaches every top-level command and lets nested packages register
// their own children.
func Register(root *cobra.Command) {
	actions.Register(root)
	config.Register(root)
	plays.Register(root)
	scene.Register(root)
	command.Register(root, exportassets.NewCommand, initialize.NewCommand, options.NewCommand, render.NewCommand, serve.NewCommand, version.NewCommand)
}
