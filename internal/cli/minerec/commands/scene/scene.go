package scene

import (
	"github.com/proj-airi/mc-play-recorder/internal/cli/minerec/command"
	scenedescribe "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/scene/describe"
	sceneextract "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/scene/extract"
	sceneprepare "github.com/proj-airi/mc-play-recorder/internal/cli/minerec/commands/scene/prepare"
	"github.com/spf13/cobra"
)

func Register(root *cobra.Command) {
	parent := NewCommand()
	command.Register(parent, scenedescribe.NewCommand, sceneextract.NewCommand, sceneprepare.NewCommand)
	root.AddCommand(parent)
}
