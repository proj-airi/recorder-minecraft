package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"github.com/proj-airi/recorder-minecraft/pkg/entsqlite"
)

type EntityVersion struct {
	ent.Schema
}

func (EntityVersion) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "entity_versions",
			Checks: map[string]string{
				"positive_version_id": "version_id > 0",
				"tick_order":          "start_tick < end_tick",
				"ordered_bounds":      "min_x <= max_x AND min_y <= max_y AND min_z <= max_z",
			},
		},
		entsqlite.Annotation{RTree: &entsqlite.RTree{
			Name: "entity_versions_rtree",
			Key:  "id",
			Dimensions: []entsqlite.Dimension{
				{Name: "tick", Min: "start_tick", Max: "end_tick"},
				{Name: "x", Min: "min_x", Max: "max_x"},
				{Name: "y", Min: "min_y", Max: "max_y"},
				{Name: "z", Min: "min_z", Max: "max_z"},
			},
		}},
	}
}

func (EntityVersion) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("blob", Blob.Type).Field("blob_sha256").Unique().Required(),
	}
}

func (EntityVersion) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int64("id").StorageKey("version_id").Positive().Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("instance_id"),
		field.Int64("network_id").Optional().Nillable(),
		field.String("dimension"),
		field.String("type_id"),
		field.Int64("start_tick"),
		field.Int64("end_tick"),
		field.Float("min_x"),
		field.Float("min_y"),
		field.Float("min_z"),
		field.Float("max_x"),
		field.Float("max_y"),
		field.Float("max_z"),
		field.String("blob_sha256").MaxLen(64),
	}
}

func (EntityVersion) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("instance_id", "start_tick").Unique(),
		index.Fields("dimension", "start_tick", "end_tick"),
		index.Fields("network_id", "start_tick", "end_tick", "instance_id"),
	}
}
