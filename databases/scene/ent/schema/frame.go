package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
)

type Frame struct {
	ent.Schema
}

func (Frame) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{Table: "frames"},
	}
}

func (Frame) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("payload", Blob.Type).Field("payload_sha256").Unique().Required(),
	}
}

func (Frame) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int64("id").StorageKey("server_tick").Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("frame_id").Unique(),
		field.Int64("replay_tick").Optional().Nillable(),
		field.String("dimension"),
		field.Float("subject_x"),
		field.Float("subject_y"),
		field.Float("subject_z"),
		field.Bool("coverage_complete"),
		field.String("payload_sha256").MaxLen(64),
	}
}
