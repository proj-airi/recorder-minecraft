package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
)

type SceneMeta struct {
	ent.Schema
}

func (SceneMeta) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "scene_meta",
			Checks: map[string]string{
				"singleton":  "singleton = 1",
				"tick_order": "start_tick <= end_tick",
			},
		},
	}
}

func (SceneMeta) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int("id").StorageKey("singleton").Annotations(entsql.Annotation{Incremental: &incremental}),
		field.String("session_id"),
		field.String("player_uuid").MaxLen(36),
		field.String("connection_id").MaxLen(36),
		field.Int64("start_tick"),
		field.Int64("end_tick"),
		field.Bytes("source_replays_json"),
		field.Bool("sensitive"),
		field.Bytes("provenance_json"),
	}
}
