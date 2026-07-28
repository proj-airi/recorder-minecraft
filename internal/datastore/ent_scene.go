package datastore

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"
	_ "github.com/glebarez/go-sqlite"
	sceneent "github.com/proj-airi/mc-play-recorder/databases/minerec-scene/ent"
	"github.com/proj-airi/mc-play-recorder/databases/minerec-scene/ent/migrate"
	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/proj-airi/mc-play-recorder/pkg/filelock"
	"github.com/samber/do/v2"
)

type Scene struct {
	*sceneent.Client

	target    string
	staging   string
	lock      *filelock.Lock
	overwrite bool
	closed    bool
	published bool
}

// NewSceneDatabase prepares one writable, automatically migrated Scene Store.
// The injector owns its Ent client, staging file, and processor lock.
func NewSceneDatabase(name string, overwrite bool) func(do.Injector) (*Scene, error) {
	return func(injector do.Injector) (*Scene, error) {
		config, err := do.Invoke[*configs.Config](injector)
		if err != nil {
			return nil, err
		}
		lock, err := filelock.Acquire(filepath.Join(config.Paths.Runtime, "operation.lock"), "scene_extract")
		if err != nil {
			return nil, err
		}
		failed := true
		defer func() {
			if failed {
				_ = lock.Close()
			}
		}()
		target, err := writableTarget(context.Background(), name, overwrite)
		if err != nil {
			return nil, err
		}

		temporary, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+".tmp-*.sqlite3")
		if err != nil {
			return nil, fmt.Errorf("create staged scene database: %w", err)
		}
		staging := temporary.Name()
		if err := temporary.Close(); err != nil {
			_ = os.Remove(staging)
			return nil, fmt.Errorf("close staged scene database: %w", err)
		}

		client, err := openWritableScene(context.Background(), staging)
		if err != nil {
			_ = os.Remove(staging)
			return nil, err
		}
		scene := &Scene{Client: client, target: target, staging: staging, lock: lock, overwrite: overwrite}
		failed = false
		return scene, nil
	}
}

func openWritableScene(ctx context.Context, path string) (*sceneent.Client, error) {
	dsn := (&url.URL{Scheme: "file", Path: path, RawQuery: "_pragma=foreign_keys(1)"}).String()
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open scene database: %w", err)
	}
	if _, err := db.ExecContext(ctx, "PRAGMA journal_mode = DELETE; PRAGMA synchronous = FULL"); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("configure scene database durability: %w", err)
	}
	driver := entsql.OpenDB(dialect.SQLite, db)
	client := sceneent.NewClient(sceneent.Driver(driver))
	if err := client.Schema.Create(ctx, migrate.WithSQLite(db)); err != nil {
		_ = client.Close()
		return nil, fmt.Errorf("migrate Scene Store V2: %w", err)
	}
	return client, nil
}

// OpenScene opens an existing artifact read-only for inspection. Writable
// command paths use NewSceneDatabase so migration and cleanup remain injector-owned.
func OpenScene(path string) (*sceneent.Client, error) {
	if path == "" {
		return nil, errors.New("scene database path is required")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("resolve scene database path: %w", err)
	}
	dsn := (&url.URL{Scheme: "file", Path: absolute, RawQuery: "mode=ro"}).String()
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open scene database: %w", err)
	}
	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("open scene database: %w", err)
	}
	driver := entsql.OpenDB(dialect.SQLite, db)
	return sceneent.NewClient(sceneent.Driver(driver)), nil
}

// WithTx commits a complete scene import or rolls it back as one operation.
func (scene *Scene) WithTx(ctx context.Context, write func(*sceneent.Tx) error) (err error) {
	tx, err := scene.Tx(ctx)
	if err != nil {
		return err
	}
	defer func() {
		if recovered := recover(); recovered != nil {
			_ = tx.Rollback()
			panic(recovered)
		}
	}()
	if err = write(tx); err != nil {
		if rollbackErr := tx.Rollback(); rollbackErr != nil {
			return fmt.Errorf("%w: rollback scene transaction: %v", err, rollbackErr)
		}
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit scene transaction: %w", err)
	}
	return nil
}

func (scene *Scene) Path() string {
	return scene.target
}

// Publish closes the Ent client before atomically exposing the staged store.
func (scene *Scene) Publish() error {
	if scene.published {
		return nil
	}
	if err := scene.closeClient(); err != nil {
		return err
	}
	file, err := os.OpenFile(scene.staging, os.O_RDONLY, 0)
	if err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if scene.overwrite {
		if err := os.Rename(scene.staging, scene.target); err != nil {
			return fmt.Errorf("publish scene store: %w", err)
		}
	} else if err := os.Link(scene.staging, scene.target); err != nil {
		return fmt.Errorf("publish scene store: %w", err)
	} else if err := os.Remove(scene.staging); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(scene.target))
	if err != nil {
		return err
	}
	err = directory.Sync()
	closeErr := directory.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	scene.published = true
	return nil
}

func (scene *Scene) Close() error {
	clientErr := scene.closeClient()
	if !scene.published {
		_ = os.Remove(scene.staging)
	}
	lockErr := scene.lock.Close()
	if clientErr != nil {
		return clientErr
	}
	return lockErr
}

// Shutdown lets the command-scoped injector release the Ent client, staging
// file, and processor lock in dependency order.
func (scene *Scene) Shutdown() error {
	return scene.Close()
}

func (scene *Scene) closeClient() error {
	if scene.closed {
		return nil
	}
	scene.closed = true
	return scene.Client.Close()
}

func writableTarget(ctx context.Context, path string, overwrite bool) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	resolvedParent, err := filepath.EvalSymlinks(filepath.Dir(absolute))
	if err != nil {
		return "", fmt.Errorf("resolve scene output parent: %w", err)
	}
	absolute = filepath.Join(resolvedParent, filepath.Base(absolute))
	parent, err := os.Stat(resolvedParent)
	if err != nil || !parent.IsDir() {
		return "", errors.New("scene output parent is not a safe directory")
	}
	info, err := os.Lstat(absolute)
	if errors.Is(err, os.ErrNotExist) {
		return absolute, nil
	}
	if err != nil {
		return "", err
	}
	if !overwrite {
		return "", fmt.Errorf("scene output exists: %s; pass --overwrite to replace it", absolute)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("refusing to replace non-regular scene output")
	}
	owned, err := isSceneStore(ctx, absolute)
	if err != nil {
		return "", err
	}
	if !owned {
		return "", errors.New("refusing to replace an unowned SQLite file")
	}
	return absolute, nil
}

func isSceneStore(ctx context.Context, path string) (bool, error) {
	dsn := (&url.URL{Scheme: "file", Path: path, RawQuery: "mode=ro"}).String()
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return false, fmt.Errorf("open scene database: %w", err)
	}
	defer func() { _ = db.Close() }()
	var schemaName string
	if err := db.QueryRowContext(ctx, `SELECT schema_name FROM schema_info WHERE singleton = 1`).Scan(&schemaName); err != nil {
		return false, nil
	}
	return schemaName == "mc-recorder-scene-store-v2", nil
}
