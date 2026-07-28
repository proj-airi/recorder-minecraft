package models

import (
	"github.com/proj-airi/mc-play-recorder/internal/models/actions"
	"github.com/proj-airi/mc-play-recorder/internal/models/captures"
	"github.com/proj-airi/mc-play-recorder/internal/models/renders"
	"github.com/proj-airi/mc-play-recorder/internal/models/replays"
	"github.com/proj-airi/mc-play-recorder/internal/models/scenes"
	"github.com/samber/do/v2"
)

// Package registers lazy domain services. Commands instantiate only the
// dependency chain they actually invoke.
func Package(injector do.Injector) {
	do.Provide(injector, captures.NewService)
	do.Provide(injector, replays.NewService)
	do.Provide(injector, actions.NewService)
	do.Provide(injector, renders.NewService)
	do.Provide(injector, scenes.NewReader)
	do.Provide(injector, scenes.NewExtractor)
	do.Provide(injector, scenes.NewFinalizer)
}
