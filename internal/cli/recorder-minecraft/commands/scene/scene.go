package scene

import (
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	scenedescribe "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/scene/describe"
	sceneextract "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/scene/extract"
	sceneprepare "github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/commands/scene/prepare"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, scenedescribe.NewCommand, sceneextract.NewCommand, sceneprepare.NewCommand)
	root.AddCommand(parent)
}
