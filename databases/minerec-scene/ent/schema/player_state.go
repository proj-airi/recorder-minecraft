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

type PlayerState struct {
	ent.Schema
}

func (PlayerState) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{Table: "player_states"},
		entsqlite.Annotation{ForeignKeys: []entsqlite.ForeignKey{
			{
				Symbol:         "fk_player_states_server_tick_frames",
				Field:          "id",
				Reference:      "Frame",
				ReferenceField: "id",
			},
		}},
	}
}

func (PlayerState) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("entity_version", EntityVersion.Type).Field("entity_version_id").Unique().Required(),
		edge.To("payload", Blob.Type).Field("payload_sha256").Unique().Required(),
	}
}

func (PlayerState) Fields() []ent.Field {
	incremental := false
	return []ent.Field{
		field.Int64("id").StorageKey("server_tick").Annotations(entsql.Annotation{Incremental: &incremental}),
		field.Int64("entity_version_id"),
		field.String("entity_instance_id"),
		field.Int64("entity_id"),
		field.String("dimension"),
		field.Float("position_x"),
		field.Float("position_y"),
		field.Float("position_z"),
		field.Float("velocity_x"),
		field.Float("velocity_y"),
		field.Float("velocity_z"),
		field.Float("yaw"),
		field.Float("pitch"),
		field.Float("head_yaw"),
		field.Bool("alive"),
		field.Bool("on_ground"),
		field.String("pose").MaxLen(64),
		field.Bool("sprinting"),
		field.Bool("sneaking"),
		field.Bool("swimming"),
		field.Bool("fall_flying"),
		field.Bool("using_item"),
		field.Int("use_item_remaining_ticks"),
		field.String("game_mode").MaxLen(64),
		field.Float("health"),
		field.Float("max_health"),
		field.Float("absorption"),
		field.Int("armor"),
		field.Int("air"),
		field.Int("max_air"),
		field.Int("food_level"),
		field.Float("saturation"),
		field.Int("experience_level"),
		field.Float("experience_progress"),
		field.Int("total_experience"),
		field.Int("selected_slot"),
		field.Int64("state_barrier_apply_sequence"),
		field.String("payload_sha256").MaxLen(64),
	}
}

func (PlayerState) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("entity_instance_id"),
	}
}
