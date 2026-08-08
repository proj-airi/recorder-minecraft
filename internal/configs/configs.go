package configs

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
	"github.com/pelletier/go-toml/v2"
	"github.com/samber/do/v2"
)

const (
	Version                       = 1
	DefaultPath                   = "recorder.toml"
	defaultRuntimePath            = ".recorder/minecraft/runtime"
	defaultSceneExtractor         = "processors/scene-extractor/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"
	legacyDefaultSceneExtractor   = "mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"
	legacyGeneratedSceneExtractor = "mods/scene-extractor-mod/build/install/recorder-minecraft-scene-extractor/bin/recorder-minecraft-scene-extractor"
)

type Server struct {
	Name       string `toml:"name,omitempty" json:"name"`
	InstanceID string `toml:"instance_id" json:"instance_id"`
	EULA       bool   `toml:"eula" json:"eula"`
}

type Paths struct {
	Artifacts string `toml:"artifacts" json:"artifacts"`
	Runtime   string `toml:"runtime" json:"runtime"`
}

type Mods struct {
	RecorderProject string `toml:"recorder_project" json:"recorder_project"`
	RendererProject string `toml:"renderer_project" json:"renderer_project"`
}

type Processors struct {
	SceneExtractorExecutable string `toml:"scene_extractor_executable" json:"scene_extractor_executable"`
}

type modsFile struct {
	RecorderProject string `toml:"recorder_project"`
	RendererProject string `toml:"renderer_project"`
	// Keep accepting the old processor fields so existing version 1 configurations remain loadable.
	LegacySceneExtractorProject    string `toml:"scene_extractor_project"`
	LegacySceneExtractorExecutable string `toml:"scene_extractor_executable"`
}

type fileConfig struct {
	Version    int        `toml:"version"`
	Server     Server     `toml:"server"`
	Paths      Paths      `toml:"paths"`
	Mods       modsFile   `toml:"mods"`
	Processors Processors `toml:"processors"`
}

type Config struct {
	Source     string     `json:"source"`
	Server     Server     `json:"server"`
	Paths      Paths      `json:"paths"`
	Mods       Mods       `json:"mods"`
	Processors Processors `json:"processors"`
}

func Package(path string) func(do.Injector) {
	return func(injector do.Injector) {
		do.Provide(injector, func(do.Injector) (*Config, error) { return Load(path) })
	}
}

func Load(path string) (*Config, error) {
	source, err := filepath.Abs(expandHome(filepath.FromSlash(path)))
	if err != nil {
		return nil, fmt.Errorf("resolve configuration: %w", err)
	}
	info, err := os.Lstat(source)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("configuration not found: %s; run 'recorder-minecraft init' first", source)
		}
		return nil, fmt.Errorf("inspect configuration: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("configuration must be a non-symlinked regular file: %s", source)
	}
	raw, err := os.ReadFile(source)
	if err != nil {
		return nil, fmt.Errorf("read configuration: %w", err)
	}
	var file fileConfig
	decoder := toml.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&file); err != nil {
		return nil, fmt.Errorf("decode configuration: %w", describeUnknownFields(err))
	}
	if file.Version != Version {
		return nil, fmt.Errorf("unsupported recorder.toml version %d; expected %d", file.Version, Version)
	}
	if file.Server.Name == "" {
		file.Server.Name, err = os.Hostname()
		if err != nil || strings.TrimSpace(file.Server.Name) == "" {
			return nil, errors.New("machine hostname is empty; configure server.name explicitly")
		}
	}
	parsedID, err := uuid.Parse(file.Server.InstanceID)
	if err != nil || parsedID.String() != file.Server.InstanceID {
		return nil, errors.New("configuration value 'server.instance_id' must be a canonical UUID")
	}
	base := filepath.Dir(source)
	file.Paths.Artifacts, err = resolve(base, defaultString(file.Paths.Artifacts, "artifacts"))
	if err != nil {
		return nil, err
	}
	file.Paths.Runtime, err = resolve(base, defaultString(file.Paths.Runtime, defaultRuntimePath))
	if err != nil {
		return nil, err
	}
	if nested(file.Paths.Artifacts, file.Paths.Runtime) {
		return nil, errors.New("artifacts and runtime paths must be separate and non-nested")
	}
	file.Mods.RecorderProject, _ = resolve(base, defaultString(file.Mods.RecorderProject, "mods/recorder-mod"))
	file.Mods.RendererProject, _ = resolve(base, defaultString(file.Mods.RendererProject, "mods/renderer-mod"))
	if file.Processors.SceneExtractorExecutable != "" && file.Mods.LegacySceneExtractorExecutable != "" {
		return nil, errors.New("configure scene_extractor_executable under [processors], not both [processors] and [mods]")
	}
	executable := defaultString(file.Processors.SceneExtractorExecutable, file.Mods.LegacySceneExtractorExecutable)
	if executable == legacyDefaultSceneExtractor || executable == legacyGeneratedSceneExtractor {
		executable = defaultSceneExtractor
	}
	file.Processors.SceneExtractorExecutable, _ = resolve(base, defaultString(executable, defaultSceneExtractor))
	return &Config{
		Source: source,
		Server: file.Server,
		Paths:  file.Paths,
		Mods: Mods{
			RecorderProject: file.Mods.RecorderProject,
			RendererProject: file.Mods.RendererProject,
		},
		Processors: file.Processors,
	}, nil
}

func describeUnknownFields(err error) error {
	var missing *toml.StrictMissingError
	if !errors.As(err, &missing) {
		return err
	}

	fields := make([]string, 0, len(missing.Errors))
	for _, detail := range missing.Errors {
		line, _ := detail.Position()
		fields = append(fields, fmt.Sprintf("%s (line %d)", strings.Join(detail.Key(), "."), line))
	}
	return fmt.Errorf("unsupported configuration fields: %s", strings.Join(fields, ", "))
}

func Initialize(path string, acceptEULA bool, overwrite bool) (string, error) {
	requested := expandHome(filepath.FromSlash(path))
	if info, err := os.Lstat(requested); err == nil {
		if info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("refusing to initialize through a symlinked configuration path: %s", requested)
		}
		if !overwrite {
			return "", fmt.Errorf("refusing to overwrite existing configuration: %s", requested)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("inspect configuration path: %w", err)
	}
	target, err := filepath.Abs(requested)
	if err != nil {
		return "", fmt.Errorf("resolve configuration path: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
		return "", fmt.Errorf("create configuration directory: %w", err)
	}
	content := DefaultText(acceptEULA, uuid.NewString())
	temporary, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+".*.inprogress")
	if err != nil {
		return "", fmt.Errorf("create temporary configuration: %w", err)
	}
	temporaryName := temporary.Name()
	defer func() { _ = os.Remove(temporaryName) }()
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return "", err
	}
	if _, err := temporary.WriteString(content); err != nil {
		_ = temporary.Close()
		return "", fmt.Errorf("write configuration: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return "", fmt.Errorf("sync configuration: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return "", fmt.Errorf("close configuration: %w", err)
	}
	if err := os.Rename(temporaryName, target); err != nil {
		return "", fmt.Errorf("publish configuration: %w", err)
	}
	config, err := Load(target)
	if err != nil {
		return "", err
	}
	for _, directory := range []string{config.Paths.Artifacts, config.Paths.Runtime} {
		if err := os.MkdirAll(directory, 0o750); err != nil {
			return "", fmt.Errorf("create managed directory %s: %w", directory, err)
		}
	}
	return target, nil
}

func DefaultText(acceptEULA bool, instanceID string) string {
	return fmt.Sprintf(`version = 1

[server]
# Optional artifact display name. When omitted, the machine hostname is used.
# name = "minecraft"
# Accept https://aka.ms/MinecraftEULA before provisioning a recorder server.
instance_id = %q
eula = %t

[paths]
# Processing scratch remains outside recorder artifacts.
artifacts = "artifacts"
runtime = ".recorder/minecraft/runtime"

[mods]
recorder_project = "mods/recorder-mod"
renderer_project = "mods/renderer-mod"

[processors]
scene_extractor_executable = "processors/scene-extractor/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"
`, instanceID, acceptEULA)
}

func resolve(base, path string) (string, error) {
	// Recorder configuration uses forward slashes so the same file can move
	// between Unix and Windows hosts. Convert them before inspecting the path.
	path = expandHome(filepath.FromSlash(path))
	if !filepath.IsAbs(path) {
		path = filepath.Join(base, path)
	}
	resolved, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve path %q: %w", path, err)
	}
	return filepath.Clean(resolved), nil
}

func nested(left, right string) bool {
	if left == right {
		return true
	}
	relative, err := filepath.Rel(left, right)
	if err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return true
	}
	relative, err = filepath.Rel(right, left)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func defaultString(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~"+string(filepath.Separator)) {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(path, "~"+string(filepath.Separator)))
		}
	}
	return path
}
