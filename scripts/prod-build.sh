#!/usr/bin/env bash
# Build the production NetBox image (NetBox + our plugins baked in).
#
# This is the step to add to the monthly AMI bake, after the repo is checked
# out, so instances boot with the image already present instead of building it
# at first boot (see docs/FIRST-BOOT.md — we do not use ECR).
#
#   ./scripts/prod-build.sh              build and verify
#   ./scripts/prod-build.sh --tag NAME   build under a different tag
#   ./scripts/prod-build.sh --no-verify  skip the plugin load check
#
# Deliberately uses `docker build` rather than compose: the image has no
# dependency on any env file, while the prod compose chain expects
# env/prod.env, which lives on the data disk and is absent at bake time.
set -euo pipefail
cd "$(dirname "$0")/.."

verify=true
tag=''
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) tag="$2"; shift 2 ;;
    --no-verify) verify=false; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Derive the tag from the Dockerfile's base image so the two cannot drift.
base_image=$(grep -m1 '^FROM ' Dockerfile-Plugins | awk '{print $2}')
base_tag=${base_image##*:}
image=${tag:-netbox-custom:$base_tag}

echo "==> base image:  $base_image"
echo "==> building:    $image"
docker build -f Dockerfile-Plugins -t "$image" .

if [ "$verify" = true ]; then
  echo '==> verifying the plugins load in the built image'
  # A plugin that imports cleanly at build time but explodes on boot would
  # otherwise only surface during a redeploy, so check it here.
  docker run --rm --entrypoint /opt/netbox/venv/bin/python "$image" -c "
import os, sys
sys.path.insert(0, '/opt/netbox/netbox')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'netbox.settings')
os.environ.setdefault('SECRET_KEY', 'x' * 50)
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('REDIS_HOST', 'localhost')
os.environ.setdefault('REDIS_CACHE_HOST', 'localhost')
import django
django.setup()
from django.conf import settings
for name in settings.PLUGINS:
    __import__(name)
    print('  plugin OK:', name)
" || { echo 'plugin verification FAILED — do not ship this image' >&2; exit 1; }
fi

echo ''
echo "built: $image"
docker image inspect "$image" --format '  size: {{.Size}} bytes   created: {{.Created}}'
echo ''
echo 'Next:'
echo '  - bake this into the AMI (the image must exist locally on the instance), or'
echo '  - leave PROD_IMAGE unset in the data-disk .env and bootstrap will build at'
echo '    first boot instead (~5 minutes slower).'
echo '  - compose/prod.yml defaults to this exact tag, so no .env change is needed'
echo "    unless you passed --tag."
