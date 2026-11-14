# ADR 0002: SeaweedFS for local S3-compatible object storage

- Status: accepted (pending owner confirmation)
- Date: 2026-11-11

## Context

The spec puts the bronze layer in object storage: MinIO locally, Cloudflare R2 or S3 in the
cloud. While building Milestone 1, the official `minio/minio` image could no longer be pulled
from Docker Hub: MinIO stopped publishing free community Docker images in 2025. The
remaining options were a third-party MinIO build, compiling MinIO from source, or another
S3-compatible server.

## Decision

Run SeaweedFS (`chrislusf/seaweedfs`, Apache-2.0, image published by its maintainer) as the
local S3 endpoint on port 9000, with credentials written from `.env` at start-up and a one-shot
`s3-init` service that creates the bronze bucket idempotently.

## Consequences

- PitWall code only uses the S3 API through `PITWALL_S3_*` settings, so switching to MinIO,
  R2 or AWS S3 is a configuration change, not a code change.
- There is no MinIO-style web console; `http://localhost:9333` shows cluster status and any S3
  client (aws CLI, rclone, DuckDB `httpfs`) can browse the bucket.

## Alternatives considered

- **Community MinIO build** (e.g. `pgsty/minio`): keeps the console, but a third-party binary
  for a storage server needs vetting first.
- **MinIO compiled from source** in a Go build stage: official code, slower first `make up`.
- **Garage / RustFS**: viable S3 servers; SeaweedFS chosen for maturity.
