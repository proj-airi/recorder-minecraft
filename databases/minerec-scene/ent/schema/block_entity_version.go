package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"github.com/proj-airi/mc-play-recorder/pkg/entsqlite"
)

type BlockEntityVersion struct {
	ent.Schema
}

func (BlockEntityVersion) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "block_entity_versions",
			Checks: map[string]string{
				"positive_version_id": "version_id > 0",
				"tick_order":          "start_tick < end_tick",
			},
		},
		entsqlite.Annotation{RTree: &entsqlite.RTree{
			Name: "block_entity_versions_rtree",
			Key:  "id",
			Dimensions: []entsqlite.Dimension{
				{Name: "tick", Min: "start_tick", Max: "end_tick"},
				{Name: "x", Min: "block_x", Max: "block_x", MaxOffset: 1},
				{Name: "y", Min: "block_y", Max: "block_y", MaxOffset: 1},
				{Name: "z", Min: "block_z", Max: "block_z", MaxOffset: 1},
			},
		}},
	}
}

func (BlockEntityVersion) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("blob", Blob.Type).Field("blob_sha256").Unique().Required(),
	}
}

func (BlockEntityVersion) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int64("id").StorageKey("version_id").Positive().Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("dimension"),
		field.Int64("block_x"),
		field.Int64("block_y"),
		field.Int64("block_z"),
		field.String("type_id"),
		field.Int64("start_tick"),
		field.Int64("end_tick"),
		field.String("blob_sha256").MaxLen(64),
	}
}

func (BlockEntityVersion) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("dimension", "block_x", "block_y", "block_z", "start_tick").Unique(),
		index.Fields("dimension", "start_tick", "end_tick"),
	}
}
