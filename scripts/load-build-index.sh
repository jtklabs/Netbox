# Sourced by the build entry points after changing to the repository root.
# Let Compose parse dotenv quoting/interpolation, then explicitly export just
# the index: some Compose builders otherwise pass an empty environment secret.
load_python_build_index() {
  local env_file=${1:-.env}
  local env_args=(--env-file /dev/null)
  if [ -f "$env_file" ]; then
    env_args=(--env-file "$env_file")
  elif [ "$env_file" != .env ]; then
    echo "Build env file not found: $env_file" >&2
    return 1
  fi

  # A minimal project avoids requiring the runtime env/prod.env during AMI
  # builds. Nothing from the env file is executed as shell code or printed.
  PYTHON_INDEX_URL=$(
    printf 'services: {}\n' |
      docker compose --project-directory "$PWD" "${env_args[@]}" -f - config --environment |
      sed -n 's/^PYTHON_INDEX_URL=//p'
  ) || return 1
  if [ -z "$PYTHON_INDEX_URL" ]; then
    echo "PYTHON_INDEX_URL is empty or missing after resolving $env_file and the process environment." >&2
    echo 'Set it in that env file. An exported empty PYTHON_INDEX_URL overrides the file; unset it first.' >&2
    return 1
  fi
  export PYTHON_INDEX_URL
}
