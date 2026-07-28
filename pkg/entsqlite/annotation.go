package entsqlite

const annotationName = "SQLiteSpatial"

// Annotation describes SQLite resources that Ent's table graph cannot model.
type Annotation struct {
	RTree       *RTree       `json:"rtree,omitempty"`
	ForeignKeys []ForeignKey `json:"foreign_keys,omitempty"`
}

func (Annotation) Name() string {
	return annotationName
}

type RTree struct {
	Name       string      `json:"name"`
	Key        string      `json:"key"`
	Dimensions []Dimension `json:"dimensions"`
}

type Dimension struct {
	Name      string `json:"name"`
	Min       string `json:"min"`
	Max       string `json:"max"`
	MaxOffset int    `json:"max_offset,omitempty"`
}

type ForeignKey struct {
	Symbol         string `json:"symbol"`
	Field          string `json:"field"`
	Reference      string `json:"reference"`
	ReferenceField string `json:"reference_field"`
}
