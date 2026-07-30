package exportassets

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/proj-airi/recorder-minecraft/internal/bundled"
	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/spf13/cobra"
)

type exportOptions struct {
	Output    string
	Overwrite bool
}

func NewCommand() *cobra.Command {
	options := exportOptions{}
	cmd := &cobra.Command{
		Use:   "export-assets [directory]",
		Short: "Export artifacts embedded in this recorder-minecraft binary",
		Long: `Export the renderer mod JAR and scene extractor distribution embedded
in a release build. Development builds do not carry these generated payloads.`,
		GroupID: command.OtherGroup,
		Args:    cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 {
				options.Output = args[0]
			}
			items, err := bundled.Artifacts()
			if err != nil {
				return err
			}
			return export(cmd, options, items)
		},
	}
	cmd.Flags().StringVarP(&options.Output, "output", "o", "recorder-minecraft-artifacts", "Destination directory")
	cmd.Flags().BoolVar(&options.Overwrite, "overwrite", false, "Replace existing artifact files")
	return cmd
}

func export(cmd *cobra.Command, options exportOptions, items []bundled.Artifact) error {
	if err := os.MkdirAll(options.Output, 0o755); err != nil {
		return fmt.Errorf("create artifact directory: %w", err)
	}

	for _, item := range items {
		path := filepath.Join(options.Output, item.Name)
		if !options.Overwrite {
			_, err := os.Stat(path)
			if err == nil {
				return fmt.Errorf("artifact already exists: %s; pass --overwrite to replace it", path)
			}
			if !errors.Is(err, fs.ErrNotExist) {
				return fmt.Errorf("inspect artifact destination %s: %w", path, err)
			}
		}
	}

	for _, item := range items {
		path := filepath.Join(options.Output, item.Name)
		if err := writeFile(path, item.Data, options.Overwrite); err != nil {
			return err
		}
		digest := sha256.Sum256(item.Data)
		if _, err := fmt.Fprintf(cmd.OutOrStdout(), "%x  %s\n", digest, path); err != nil {
			return err
		}
	}
	return nil
}

func writeFile(path string, data []byte, overwrite bool) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".recorder-minecraft-artifact-*")
	if err != nil {
		return fmt.Errorf("create temporary artifact for %s: %w", path, err)
	}
	temporaryPath := temporary.Name()
	defer func() {
		_ = os.Remove(temporaryPath)
	}()

	if _, err := temporary.Write(data); err != nil {
		return errors.Join(
			fmt.Errorf("write artifact %s: %w", path, err),
			closeError(temporary, path),
		)
	}
	if err := temporary.Chmod(0o644); err != nil {
		return errors.Join(
			fmt.Errorf("set artifact permissions %s: %w", path, err),
			closeError(temporary, path),
		)
	}
	if err := closeError(temporary, path); err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		// Windows does not let Rename atomically replace an existing file.
		if !overwrite || os.Remove(path) != nil {
			return fmt.Errorf("replace artifact %s: %w", path, err)
		}
		if err := os.Rename(temporaryPath, path); err != nil {
			return fmt.Errorf("replace artifact %s: %w", path, err)
		}
	}
	return nil
}

func closeError(file *os.File, path string) error {
	if err := file.Close(); err != nil {
		return fmt.Errorf("close artifact %s: %w", path, err)
	}
	return nil
}
