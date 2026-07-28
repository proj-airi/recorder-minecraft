package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
)

type SectionVersion struct {
	ent.Schema
}

func (SectionVersion) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "section_versions",
			Checks: map[string]string{
				"positive_version_id": "version_id > 0",
				"tick_order":          "start_tick < end_tick",
			},
		},
	}
}

func (SectionVersion) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("blob", Blob.Type).Field("blob_sha256").Unique().Required(),
	}
}

func (SectionVersion) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int64("id").StorageKey("version_id").Positive().Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("dimension"),
		field.Int64("section_x"),
		field.Int64("section_y"),
		field.Int64("section_z"),
		field.Int64("start_tick"),
		field.Int64("end_tick"),
		field.String("blob_sha256").MaxLen(64),
	}
}

func (SectionVersion) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("dimension", "section_x", "section_y", "section_z", "start_tick").Unique(),
		index.Fields("dimension", "start_tick", "end_tick"),
	}
}
