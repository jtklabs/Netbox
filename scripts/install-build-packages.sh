#!/bin/sh
# The index exists only for this BuildKit RUN; it is never image configuration.
set -eu
index_file=/run/secrets/python_index_url
if ! UV_DEFAULT_INDEX=$(cat "$index_file" 2>/dev/null); then
    echo 'The Python index build secret could not be read. Build with scripts/compose-build.sh.' >&2
    exit 1
fi
if [ -z "$UV_DEFAULT_INDEX" ]; then
    echo 'The Python index build secret is empty. Build with scripts/compose-build.sh to forward PYTHON_INDEX_URL from .env.' >&2
    exit 1
fi
# Override the default index, rather than adding a secondary index while
# leaving public PyPI enabled. Do not inherit alternate indexes from the base.
unset UV_INDEX UV_INDEX_URL UV_EXTRA_INDEX_URL
export UV_DEFAULT_INDEX
# Keep installer caches containing index metadata out of the image, too.
exec /usr/local/bin/uv pip install --no-config --no-cache "$@"
