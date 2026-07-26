---
name: use-counterfeiter
description: Generate and maintain Counterfeiter fakes for Go interfaces. Use when adding, updating, or testing interface fakes or mocks, especially gRPC/protobuf clients and servers, Counterfeiter generation directives, fake package placement, Go tool registration, generated-code CI checks, or lint exclusions.
---

# Use Counterfeiter

Use [Counterfeiter](https://github.com/maxbrunsfeld/counterfeiter) to generate Go interface fakes. Generated fakes make calls observable and configurable without hand-writing mock implementations that can silently diverge from their interface.

For the package-level fake API shape, follow the discoverability principle used by [Kubernetes client-go fakes](https://pkg.go.dev/k8s.io/client-go/kubernetes/fake): callers should be able to find a fake alongside the public API hierarchy, without mixing it into generated API code.

## Workflow

1. Locate the interface definition and its owning package. Generate a fake only for a real consumer boundary; do not introduce a local interface merely to make a concrete dependency mockable.
2. Choose the output package using the placement rules below. The output directory name is important: Counterfeiter derives the generated Go package name from it.
3. Add a `generate.go` file in that fake package. Each directive must name the exact fully qualified interface being generated.
4. Register Counterfeiter through the repository's Go tool convention. Use `go tool counterfeiter`, never a globally installed binary.
5. Run generation, format the handwritten generation file, and commit generated fake source with it.
6. Keep generated fakes excluded explicitly from static analysis and verify their freshness in CI.

## Fake Placement

### Generated protobuf and gRPC APIs

Put fakes for public generated gRPC clients and server interfaces under the SDK's dedicated `fake/` root. Mirror the source package identity below that root, but replace the source package's final version leaf with a fake package whose directory and package name both begin with `fake`.

For example, a source package shaped as:

```text
<sdk-root>/<module-api-path>/<domain>/<version>
```

has a fake package shaped as:

```text
<sdk-root>/fake/<module-api-path>/<domain>/fake<domain><version>
```

Preserve the meaningful path segments so the fake is easy to discover and imports do not collide. Do not put handwritten fake code beside generated `.pb.go` or `.grpc.pb.go` files. A `fake/` path and a package name beginning with `fake` keep completion, diffs, and generated-code ignore rules clear.

### Handwritten interfaces

For a non-protobuf, non-gRPC interface, create a dedicated sibling fake package beneath the interface owner's package. For an owner shaped as `pkg/sdks/<name>`, use `pkg/sdks/<name>/fake<name>`. This keeps the fake close to its contract while isolating generated code from the production package and unrelated APIs.

Do not use one catch-all fake package for unrelated handwritten interfaces. A package may contain multiple fakes only when they belong to the same API surface.

## Generation Files

Every fake package must contain a committed `generate.go` with one or more explicit directives. Name both the generated filename and the full interface name:

```go
// Package fakeordersv1 provides generated test doubles for the Orders gRPC API.
package fakeordersv1

//go:generate go tool counterfeiter -o ./fake_orders_service_client.go example.com/project/apis/sdk/go/apis/orders/v1.OrdersServiceClient
```

For several related interfaces, retain one directive per interface in the same dedicated fake package. This makes the generated API, source interface, and output filename reviewable. Do not hand-edit generated `fake_*.go` files; change the directive or source interface and regenerate instead.

## Tooling, CI, and Checks

Pin `github.com/maxbrunsfeld/counterfeiter/v6` in the root `go.mod` `tool` block and in the repository's `tools/` convention. Install or update it with:

```sh
go get -tool github.com/maxbrunsfeld/counterfeiter/v6@<version>
```

When a repository has CI, add a generation-freshness step that runs generation for the fake packages and fails when the resulting fake tree has a diff. Keep normal Go tests and static analysis in that workflow.

Add the dedicated fake roots explicitly to each static checker or formatter's generated/excluded paths. This exempts generated `fake_*.go` output while retaining the `generate.go` instructions as the source of truth.

Before finishing, run the narrowest relevant commands, then the repository checks required by the change:

```sh
go generate <fake-package-path>
go test <consumer-package-path>
golangci-lint run <changed-package-paths>
git diff --check
```

For a CI freshness check, use `git diff --exit-code -- <fake-root>` immediately after generation.

## Testing With Generated Fakes

Configure the generated fake through Counterfeiter's typed `Returns`, `ReturnsOnCall`, or `Calls` API and assert the consumer's observable behaviour. Use the fake's typed call-count and argument helpers when the interaction itself is part of the contract. Do not replace the generated fake with an ad-hoc handwritten implementation in `_test.go`.
