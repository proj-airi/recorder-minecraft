package entsqlite

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"entgo.io/ent/entc"
	"entgo.io/ent/entc/gen"
)

const generatedAnnotationName = "SQLiteSpatialConfig"

var identifierPattern = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

type Extension struct {
	entc.DefaultExtension
	version int
}

type generatedConfig struct {
	ForeignKeys []generatedForeignKey
	Statements  string
}

type generatedForeignKey struct {
	Symbol, Table, Column, Reference, ReferenceColumn string
}

func NewExtension(version int) *Extension {
	return &Extension{version: version}
}

// NOTICE: Ent extensions bundle generation hooks and templates using the
// interfaces defined in `https://github.com/ent/ent/blob/e0ba79d911cca949468293d4e2899d4c61cfc823/entc/entc.go#L210-L270`.
func (extension *Extension) Hooks() []gen.Hook {
	return []gen.Hook{func(next gen.Generator) gen.Generator {
		return gen.GenerateFunc(func(graph *gen.Graph) error {
			config, err := extension.config(graph)
			if err != nil {
				return err
			}
			if graph.Annotations == nil {
				graph.Annotations = make(gen.Annotations)
			}
			graph.Annotations[generatedAnnotationName] = config
			return next.Generate(graph)
		})
	}}
}

func (*Extension) Templates() []*gen.Template {
	return []*gen.Template{
		gen.MustParse(gen.NewTemplate("sqlite-spatial").Parse(sqliteMigrationTemplate)),
	}
}

func (extension *Extension) config(graph *gen.Graph) (generatedConfig, error) {
	config := generatedConfig{}
	var statements strings.Builder
	fmt.Fprintf(&statements, "PRAGMA user_version = %d;\n", extension.version)
	for _, node := range graph.Nodes {
		annotation, ok, err := annotation(node)
		if err != nil {
			return config, fmt.Errorf("sqlite extension: decode %s annotation: %w", node.Name, err)
		}
		if !ok {
			continue
		}
		for _, foreignKey := range annotation.ForeignKeys {
			resolved, err := resolveForeignKey(graph, node, foreignKey)
			if err != nil {
				return config, err
			}
			config.ForeignKeys = append(config.ForeignKeys, resolved)
		}
		if annotation.RTree != nil {
			statement, err := resolveRTree(node, *annotation.RTree)
			if err != nil {
				return config, err
			}
			statements.WriteString(statement)
		}
	}
	config.Statements = statements.String()
	return config, nil
}

func annotation(node *gen.Type) (Annotation, bool, error) {
	value, ok := node.Annotations[annotationName]
	if !ok {
		return Annotation{}, false, nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return Annotation{}, false, err
	}
	var annotation Annotation
	if err := json.Unmarshal(raw, &annotation); err != nil {
		return Annotation{}, false, err
	}
	return annotation, true, nil
}

func resolveForeignKey(graph *gen.Graph, node *gen.Type, foreignKey ForeignKey) (generatedForeignKey, error) {
	if !identifierPattern.MatchString(foreignKey.Symbol) {
		return generatedForeignKey{}, fmt.Errorf("sqlite extension: %s has invalid foreign-key symbol %q", node.Name, foreignKey.Symbol)
	}
	fieldColumn, err := column(node, foreignKey.Field)
	if err != nil {
		return generatedForeignKey{}, err
	}
	var reference *gen.Type
	for _, candidate := range graph.Nodes {
		if candidate.Name == foreignKey.Reference {
			reference = candidate
			break
		}
	}
	if reference == nil {
		return generatedForeignKey{}, fmt.Errorf("sqlite extension: %s references unknown Ent type %q", node.Name, foreignKey.Reference)
	}
	referenceColumn, err := column(reference, foreignKey.ReferenceField)
	if err != nil {
		return generatedForeignKey{}, err
	}
	return generatedForeignKey{
		Symbol: foreignKey.Symbol, Table: node.Table(), Column: fieldColumn,
		Reference: reference.Table(), ReferenceColumn: referenceColumn,
	}, nil
}

func resolveRTree(node *gen.Type, rtree RTree) (string, error) {
	if !identifierPattern.MatchString(rtree.Name) {
		return "", fmt.Errorf("sqlite extension: %s has invalid R-tree name %q", node.Name, rtree.Name)
	}
	key, err := column(node, rtree.Key)
	if err != nil {
		return "", err
	}
	if len(rtree.Dimensions) == 0 {
		return "", fmt.Errorf("sqlite extension: %s R-tree has no dimensions", node.Name)
	}
	columns := []string{key}
	values := []string{"new." + key}
	updates := []string{key}
	for _, dimension := range rtree.Dimensions {
		if !identifierPattern.MatchString(dimension.Name) {
			return "", fmt.Errorf("sqlite extension: %s has invalid R-tree dimension %q", node.Name, dimension.Name)
		}
		minimum, err := column(node, dimension.Min)
		if err != nil {
			return "", err
		}
		maximum, err := column(node, dimension.Max)
		if err != nil {
			return "", err
		}
		columns = append(columns, "min_"+dimension.Name, "max_"+dimension.Name)
		values = append(values, "new."+minimum, offset("new."+maximum, dimension.MaxOffset))
		updates = appendUnique(updates, minimum, maximum)
	}
	table := node.Table()
	var statement strings.Builder
	fmt.Fprintf(&statement, "CREATE VIRTUAL TABLE IF NOT EXISTS %s USING rtree(%s);\n", rtree.Name, strings.Join(columns, ","))
	fmt.Fprintf(&statement, "CREATE TRIGGER IF NOT EXISTS %s_insert AFTER INSERT ON %s BEGIN INSERT INTO %s VALUES(%s); END;\n", rtree.Name, table, rtree.Name, strings.Join(values, ","))
	fmt.Fprintf(&statement, "CREATE TRIGGER IF NOT EXISTS %s_delete AFTER DELETE ON %s BEGIN DELETE FROM %s WHERE %s=old.%s; END;\n", rtree.Name, table, rtree.Name, key, key)
	fmt.Fprintf(&statement, "CREATE TRIGGER IF NOT EXISTS %s_update AFTER UPDATE OF %s ON %s BEGIN DELETE FROM %s WHERE %s=old.%s; INSERT INTO %s VALUES(%s); END;\n", rtree.Name, strings.Join(updates, ","), table, rtree.Name, key, key, rtree.Name, strings.Join(values, ","))
	return statement.String(), nil
}

func column(node *gen.Type, name string) (string, error) {
	if node.ID != nil && (name == "id" || name == node.ID.Name || name == node.ID.StorageKey()) {
		return node.ID.StorageKey(), nil
	}
	for _, field := range node.Fields {
		if name == field.Name || name == field.StorageKey() {
			return field.StorageKey(), nil
		}
	}
	return "", fmt.Errorf("sqlite extension: %s has no field %q", node.Name, name)
}

func offset(expression string, value int) string {
	if value == 0 {
		return expression
	}
	return fmt.Sprintf("%s%+d", expression, value)
}

func appendUnique(values []string, candidates ...string) []string {
	for _, candidate := range candidates {
		found := false
		for _, value := range values {
			if value == candidate {
				found = true
				break
			}
		}
		if !found {
			values = append(values, candidate)
		}
	}
	return values
}

const sqliteMigrationTemplate = `
{{ define "migrate/sqlite_spatial" }}
{{- $config := index $.Annotations "SQLiteSpatialConfig" -}}
{{- with extend $ "Package" "migrate" }}
	{{ template "header" . }}
{{- end }}

import (
	"context"
	"database/sql"
	"fmt"

	sqlschema "entgo.io/ent/dialect/sql/schema"
)

var sqliteForeignKeys = []struct {
	symbol, table, column, reference, referenceColumn string
}{
	{{- range $foreignKey := $config.ForeignKeys }}
	{ {{ printf "%q" $foreignKey.Symbol }}, {{ printf "%q" $foreignKey.Table }}, {{ printf "%q" $foreignKey.Column }}, {{ printf "%q" $foreignKey.Reference }}, {{ printf "%q" $foreignKey.ReferenceColumn }} },
	{{- end }}
}

const sqliteResources = {{ printf "%q" $config.Statements }}

// WithSQLite installs generated SQLite resources inside Ent's migration.
func WithSQLite(db *sql.DB) sqlschema.MigrateOption {
	return sqlschema.WithHooks(func(next sqlschema.Creator) sqlschema.Creator {
		return sqlschema.CreateFunc(func(ctx context.Context, source ...*sqlschema.Table) error {
			tables, err := sqlschema.CopyTables(source)
			if err != nil {
				return fmt.Errorf("copy generated migration tables: %w", err)
			}
			byName := make(map[string]*sqlschema.Table, len(tables))
			for _, table := range tables {
				byName[table.Name] = table
			}
			for _, foreignKey := range sqliteForeignKeys {
				table, tableOK := byName[foreignKey.table]
				reference, referenceOK := byName[foreignKey.reference]
				if !tableOK || !referenceOK {
					return fmt.Errorf("generated migration is missing a table for foreign key %s", foreignKey.symbol)
				}
				column, columnOK := table.Column(foreignKey.column)
				referenceColumn, referenceColumnOK := reference.Column(foreignKey.referenceColumn)
				if !columnOK || !referenceColumnOK {
					return fmt.Errorf("generated migration is missing a column for foreign key %s", foreignKey.symbol)
				}
				table.ForeignKeys = append(table.ForeignKeys, &sqlschema.ForeignKey{
					Symbol: foreignKey.symbol, Columns: []*sqlschema.Column{column},
					RefTable: reference, RefColumns: []*sqlschema.Column{referenceColumn},
				})
			}
			if err := next.Create(ctx, tables...); err != nil {
				return err
			}
			if _, err := db.ExecContext(ctx, sqliteResources); err != nil {
				return fmt.Errorf("create generated SQLite resources: %w", err)
			}
			return nil
		})
	})
}
{{ end }}
`
