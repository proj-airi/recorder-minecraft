// Package itemicons renders inventory icons for Minecraft item ids from a local client jar.
//
// Item textures are Mojang assets that this repository must not ship. They are read at runtime
// from a client jar the operator already has (for example the one Fabric Loom caches for the
// renderer mod), and only the rendered icon of a requested item leaves the process.
package itemicons

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"regexp"
	"strings"
	"sync"
)

// ErrNotFound reports an item id the jar has no usable icon for.
var ErrNotFound = errors.New("item icon not found")

// cubeSize is the edge of a rendered block icon. Flat item textures keep their own 16px size; the
// dashboard scales both with nearest-neighbour sampling.
const cubeSize = 32

// maxModelDepth bounds parent chains; vanilla chains are at most about five models deep.
const maxModelDepth = 16

var idPart = regexp.MustCompile(`^[a-z0-9_.-]+$`)

// Source resolves and renders icons from one client jar. It is safe for concurrent use.
type Source struct {
	archive *zip.ReadCloser
	files   map[string]*zip.File

	mutex sync.Mutex
	cache map[string]cachedIcon
}

type cachedIcon struct {
	png []byte
	err error
}

// Open indexes the client jar at path. The jar stays open until Close.
func Open(path string) (*Source, error) {
	archive, err := zip.OpenReader(path)
	if err != nil {
		return nil, fmt.Errorf("open Minecraft client jar: %w", err)
	}
	files := make(map[string]*zip.File, len(archive.File))
	for _, file := range archive.File {
		if strings.HasPrefix(file.Name, "assets/") {
			files[file.Name] = file
		}
	}
	if !hasItemDefinitions(files) {
		_ = archive.Close()
		return nil, fmt.Errorf("%s has no assets/minecraft/items definitions; use a 1.21.4+ client jar, not a server jar", path)
	}
	return &Source{archive: archive, files: files, cache: map[string]cachedIcon{}}, nil
}

func hasItemDefinitions(files map[string]*zip.File) bool {
	for name := range files {
		if strings.HasPrefix(name, "assets/minecraft/items/") {
			return true
		}
	}
	return false
}

func (source *Source) Close() error {
	return source.archive.Close()
}

// Icon returns a PNG icon for a namespaced item id such as "minecraft:oak_log".
func (source *Source) Icon(namespace, path string) ([]byte, error) {
	if !idPart.MatchString(namespace) || !idPart.MatchString(path) || strings.Contains(path, "..") {
		return nil, ErrNotFound
	}
	key := namespace + ":" + path
	source.mutex.Lock()
	defer source.mutex.Unlock()
	if cached, ok := source.cache[key]; ok {
		return cached.png, cached.err
	}
	icon, err := source.render(namespace, path)
	if err == nil {
		var buffer bytes.Buffer
		if err = png.Encode(&buffer, icon); err == nil {
			source.cache[key] = cachedIcon{png: buffer.Bytes()}
			return buffer.Bytes(), nil
		}
	}
	source.cache[key] = cachedIcon{err: err}
	return nil, err
}

type itemDefinition struct {
	Model json.RawMessage `json:"model"`
}

// itemModelNode is the subset of the 1.21.4+ item model definition tree needed to reach one
// static model: plain models, the fallback of select/condition/range dispatchers, and the base
// model of special (entity-rendered) items such as chests.
type itemModelNode struct {
	Type     string          `json:"type"`
	Model    json.RawMessage `json:"model"`
	Base     string          `json:"base"`
	Fallback json.RawMessage `json:"fallback"`
	OnFalse  json.RawMessage `json:"on_false"`
	Cases    []struct {
		Model json.RawMessage `json:"model"`
	} `json:"cases"`
	Entries []struct {
		Model json.RawMessage `json:"model"`
	} `json:"entries"`
	Models []json.RawMessage `json:"models"`
	Tints  []tintSource      `json:"tints"`
}

type tintSource struct {
	Type    string          `json:"type"`
	Value   json.RawMessage `json:"value"`
	Default json.RawMessage `json:"default"`
}

type blockModel struct {
	Parent   string            `json:"parent"`
	Textures map[string]string `json:"textures"`
	Elements []modelElement    `json:"elements"`
}

type modelElement struct {
	Faces map[string]modelFace `json:"faces"`
}

type modelFace struct {
	Texture   string `json:"texture"`
	TintIndex *int   `json:"tintindex"`
}

// resolvedModel is a model with its parent chain merged: child textures override parents, and
// the first model in the chain that declares elements owns the geometry.
type resolvedModel struct {
	textures  map[string]string
	elements  []modelElement
	generated bool
}

func (source *Source) render(namespace, path string) (image.Image, error) {
	var definition itemDefinition
	if err := source.readJSON(fmt.Sprintf("assets/%s/items/%s.json", namespace, path), &definition); err != nil {
		return nil, err
	}
	modelID, tints, err := staticModel(definition.Model, nil)
	if err != nil {
		return nil, err
	}
	model, err := source.resolveModel(modelID)
	if err != nil {
		return nil, err
	}

	if layer, ok := model.texture("layer0"); ok && model.generated {
		texture, err := source.texture(layer)
		if err != nil {
			return nil, err
		}
		return tint(texture, tintColor(tints, 0)), nil
	}

	if len(model.elements) > 0 {
		faces := model.elements[0].Faces
		top, topTint := source.faceTexture(model, faces, "up")
		left, leftTint := source.faceTexture(model, faces, "south")
		right, rightTint := source.faceTexture(model, faces, "east")
		if top != nil && left != nil && right != nil {
			return renderCube(
				tint(top, tintFor(tints, topTint)),
				tint(left, tintFor(tints, leftTint)),
				tint(right, tintFor(tints, rightTint)),
			), nil
		}
	}

	// Entity-rendered items (chests, beds, shields) have no static faces; their particle texture is
	// the closest honest stand-in for a picture of the item.
	if particle, ok := model.texture("particle"); ok {
		return source.texture(particle)
	}
	return nil, ErrNotFound
}

// staticModel walks an item model definition to the model id it would draw by default.
func staticModel(raw json.RawMessage, inherited []tintSource) (string, []tintSource, error) {
	if len(raw) == 0 {
		return "", nil, ErrNotFound
	}
	var node itemModelNode
	if err := json.Unmarshal(raw, &node); err != nil {
		return "", nil, fmt.Errorf("decode item model: %w", err)
	}
	tints := inherited
	if len(node.Tints) > 0 {
		tints = node.Tints
	}
	switch strings.TrimPrefix(node.Type, "minecraft:") {
	case "model":
		var id string
		if err := json.Unmarshal(node.Model, &id); err != nil {
			return "", nil, fmt.Errorf("decode item model id: %w", err)
		}
		return id, tints, nil
	case "special":
		if node.Base == "" {
			return "", nil, ErrNotFound
		}
		return node.Base, tints, nil
	case "select", "range_dispatch":
		if len(node.Fallback) > 0 {
			return staticModel(node.Fallback, tints)
		}
		if len(node.Cases) > 0 {
			return staticModel(node.Cases[0].Model, tints)
		}
		if len(node.Entries) > 0 {
			return staticModel(node.Entries[0].Model, tints)
		}
	case "condition":
		return staticModel(node.OnFalse, tints)
	case "composite":
		if len(node.Models) > 0 {
			return staticModel(node.Models[0], tints)
		}
	}
	return "", nil, ErrNotFound
}

func (source *Source) resolveModel(id string) (resolvedModel, error) {
	model := resolvedModel{textures: map[string]string{}}
	for depth := 0; id != "" && depth < maxModelDepth; depth++ {
		namespace, path := splitID(id)
		// Flat items inherit from item/generated (directly or through item/handheld). Its own file only
		// adds display transforms, so stop there instead of requiring it to exist.
		if path == "item/generated" || path == "builtin/generated" {
			model.generated = true
			break
		}
		if strings.HasPrefix(path, "builtin/") {
			break
		}
		var current blockModel
		if err := source.readJSON(fmt.Sprintf("assets/%s/models/%s.json", namespace, path), &current); err != nil {
			return model, err
		}
		for key, value := range current.Textures {
			if _, overridden := model.textures[key]; !overridden {
				model.textures[key] = value
			}
		}
		if model.elements == nil && len(current.Elements) > 0 {
			model.elements = current.Elements
		}
		id = current.Parent
	}
	return model, nil
}

// texture follows "#name" references to a concrete texture id.
func (model resolvedModel) texture(name string) (string, bool) {
	value, ok := model.textures[name]
	for depth := 0; ok && strings.HasPrefix(value, "#") && depth < maxModelDepth; depth++ {
		value, ok = model.textures[strings.TrimPrefix(value, "#")]
	}
	return value, ok && !strings.HasPrefix(value, "#")
}

func (source *Source) faceTexture(model resolvedModel, faces map[string]modelFace, side string) (image.Image, *int) {
	face, ok := faces[side]
	if !ok {
		return nil, nil
	}
	id, ok := model.texture(strings.TrimPrefix(face.Texture, "#"))
	if !ok {
		return nil, nil
	}
	texture, err := source.texture(id)
	if err != nil {
		return nil, nil
	}
	return texture, face.TintIndex
}

// texture decodes a texture id; animated textures are vertical strips, so only the first square
// frame is kept.
func (source *Source) texture(id string) (image.Image, error) {
	namespace, path := splitID(id)
	file, ok := source.files[fmt.Sprintf("assets/%s/textures/%s.png", namespace, path)]
	if !ok {
		return nil, ErrNotFound
	}
	reader, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	decoded, err := png.Decode(reader)
	if err != nil {
		return nil, fmt.Errorf("decode texture %s: %w", id, err)
	}
	bounds := decoded.Bounds()
	if bounds.Dy() > bounds.Dx() {
		frame := image.NewNRGBA(image.Rect(0, 0, bounds.Dx(), bounds.Dx()))
		for y := range bounds.Dx() {
			for x := range bounds.Dx() {
				frame.Set(x, y, decoded.At(bounds.Min.X+x, bounds.Min.Y+y))
			}
		}
		return frame, nil
	}
	return decoded, nil
}

func (source *Source) readJSON(name string, target any) error {
	file, ok := source.files[name]
	if !ok {
		return ErrNotFound
	}
	reader, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = reader.Close() }()
	raw, err := io.ReadAll(reader)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("decode %s: %w", name, err)
	}
	return nil
}

func splitID(id string) (string, string) {
	if namespace, path, ok := strings.Cut(id, ":"); ok {
		return namespace, path
	}
	return "minecraft", id
}

// NOTICE: Grass and foliage tints depend on the biome in game. Inventory icons use the colors the
// client shows for the default temperature/downfall, so fixed values stand in for the colormap
// lookup instead of shipping or sampling the colormap textures.
var (
	defaultGrassTint   = color.NRGBA{R: 0x7c, G: 0xbd, B: 0x6b, A: 0xff}
	defaultFoliageTint = color.NRGBA{R: 0x48, G: 0xb5, B: 0x18, A: 0xff}
)

func tintFor(tints []tintSource, index *int) *color.NRGBA {
	if index == nil {
		return nil
	}
	return tintColor(tints, *index)
}

func tintColor(tints []tintSource, index int) *color.NRGBA {
	if index < 0 || index >= len(tints) {
		return nil
	}
	source := tints[index]
	switch strings.TrimPrefix(source.Type, "minecraft:") {
	case "grass":
		return &defaultGrassTint
	case "foliage":
		return &defaultFoliageTint
	case "constant":
		return packedColor(source.Value)
	default:
		// dye, firework, potion, map color, team: the definition carries the uncoloured default.
		return packedColor(source.Default)
	}
}

// packedColor decodes a tint written as a signed 0xAARRGGBB/0xRRGGBB integer.
func packedColor(raw json.RawMessage) *color.NRGBA {
	var packed int64
	if len(raw) == 0 || json.Unmarshal(raw, &packed) != nil {
		return nil
	}
	value := uint32(packed)
	return &color.NRGBA{R: uint8(value >> 16), G: uint8(value >> 8), B: uint8(value), A: 0xff}
}

func tint(texture image.Image, multiplier *color.NRGBA) image.Image {
	if multiplier == nil {
		return texture
	}
	bounds := texture.Bounds()
	tinted := image.NewNRGBA(bounds)
	for y := bounds.Min.Y; y < bounds.Max.Y; y++ {
		for x := bounds.Min.X; x < bounds.Max.X; x++ {
			pixel := color.NRGBAModel.Convert(texture.At(x, y)).(color.NRGBA)
			pixel.R = uint8(uint16(pixel.R) * uint16(multiplier.R) / 255)
			pixel.G = uint8(uint16(pixel.G) * uint16(multiplier.G) / 255)
			pixel.B = uint8(uint16(pixel.B) * uint16(multiplier.B) / 255)
			tinted.SetNRGBA(x, y, pixel)
		}
	}
	return tinted
}
