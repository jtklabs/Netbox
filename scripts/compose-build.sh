#!/usr/bin/env bash
# Build using the root .env (including symlinks) or an explicit --env-file.
# Remaining arguments are passed to docker compose build, e.g. --no-cache netbox.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=.env
compose_command=(docker compose)
if [ "${1:-}" = --env-file ]; then
  env_file=${2:?--env-file requires a path}
  compose_command+=(--env-file "$env_file")
  shift 2
fi
source scripts/load-build-index.sh
load_python_build_index "$env_file"
exec "${compose_command[@]}" build "$@"
