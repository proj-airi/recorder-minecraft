package datastore

import "github.com/samber/do/v2"

// Package binds one writable, play-local Scene Store to a command injector.
func Package(name string, overwrite bool) func(do.Injector) {
	return func(injector do.Injector) {
		do.Provide(injector, NewSceneDatabase(name, overwrite))
	}
}
