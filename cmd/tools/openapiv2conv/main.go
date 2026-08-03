// NOTICE: This utility is copied from
// `https://github.com/nekomeowww/factorio-rcon-api/blob/7d2e03b91e26ec5e6fe302eeab001f1a44ae8cf4/cmd/tools/openapiv2conv/main.go#L1-L96`.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/getkin/kin-openapi/openapi2"
	"github.com/getkin/kin-openapi/openapi2conv"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

var input, inputFormat, output, outputFormat string

func main() {
	root := &cobra.Command{
		Use:  "openapiv2conv",
		Args: cobra.NoArgs,
		RunE: run,
	}
	root.Flags().StringVarP(&input, "input", "i", "", "input file path")
	root.Flags().StringVar(&inputFormat, "input-format", "json", "input file format")
	root.Flags().StringVarP(&output, "output", "o", "", "output file path")
	root.Flags().StringVar(&outputFormat, "output-format", "yaml", "output file format")
	_ = root.MarkFlagRequired("input")
	_ = root.MarkFlagRequired("output")
	if err := root.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}

func run(*cobra.Command, []string) error {
	raw, err := os.ReadFile(input)
	if err != nil {
		return fmt.Errorf("read OpenAPI v2 input %s: %w", input, err)
	}
	document := &openapi2.T{}
	switch inputFormat {
	case "json":
		err = json.Unmarshal(raw, document)
	case "yaml":
		err = yaml.Unmarshal(raw, document)
	default:
		return fmt.Errorf("unsupported input format %q", inputFormat)
	}
	if err != nil {
		return fmt.Errorf("decode OpenAPI v2 input %s: %w", input, err)
	}
	converted, err := openapi2conv.ToV3(document)
	if err != nil {
		return fmt.Errorf("convert OpenAPI v2 to v3: %w", err)
	}
	buffer := new(bytes.Buffer)
	switch outputFormat {
	case "json":
		encoder := json.NewEncoder(buffer)
		encoder.SetIndent("", "  ")
		err = encoder.Encode(converted)
	case "yaml":
		encoder := yaml.NewEncoder(buffer)
		encoder.SetIndent(2)
		err = encoder.Encode(converted)
	default:
		return fmt.Errorf("unsupported output format %q", outputFormat)
	}
	if err != nil {
		return fmt.Errorf("encode OpenAPI v3 output: %w", err)
	}
	if output == "" {
		return errors.New("output path is empty")
	}
	if err := os.MkdirAll(filepath.Dir(output), 0o750); err != nil {
		return fmt.Errorf("create OpenAPI output directory: %w", err)
	}
	if err := os.WriteFile(output, buffer.Bytes(), 0o644); err != nil {
		return fmt.Errorf("write OpenAPI v3 output %s: %w", output, err)
	}
	return nil
}
