package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
)

type Blob struct {
	ent.Schema
}

func (Blob) Annotations() []schema.Annotation {
	return []schema.Annotation{
		entsql.Annotation{
			Table: "blobs",
			Checks: map[string]string{
				"known_kind":                    "kind IN ('section', 'entity', 'block_entity', 'frame', 'player_state')",
				"zlib_encoding":                 "encoding = 'zlib'",
				"nonnegative_uncompressed_size": "uncompressed_size >= 0",
				"nonnegative_compressed_size":   "compressed_size >= 0",
			},
		},
	}
}

func (Blob) Fields() []ent.Field {
	return []ent.Field{
		field.String("id").StorageKey("sha256").MaxLen(64),
		field.String("kind").MaxLen(32),
		field.String("encoding").MaxLen(16),
		field.Int64("uncompressed_size").NonNegative(),
		field.Int64("compressed_size").NonNegative(),
		field.Bytes("data"),
	}
}
