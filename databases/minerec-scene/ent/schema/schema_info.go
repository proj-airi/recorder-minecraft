package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
)

type SchemaInfo struct {
	ent.Schema
}

func (SchemaInfo) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "schema_info",
			Checks: map[string]string{
				"singleton":        "singleton = 1",
				"positive_version": "schema_version > 0",
			},
		},
	}
}

func (SchemaInfo) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int("id").StorageKey("singleton").Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("schema_name").MaxLen(128),
		field.Int("schema_version").Positive(),
	}
}
