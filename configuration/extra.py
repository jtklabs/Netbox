####
## Extra configuration that can't be expressed as plain environment variables.
## Everything here is env-gated so a single file serves dev and prod.
####

from os import environ

## Subpath serving (prod: https://nova.jtklabs.dev/netbox, and the dev rehearsal
## proxy). netbox-docker deliberately has no env var for this — set BASE_PATH in
## the environment (e.g. "netbox/") to activate. The reverse proxy must map
## <BASE_PATH>static/ -> /static/ and proxy app routes WITHOUT stripping the
## prefix; the compose healthcheck must be overridden to /<BASE_PATH>login/.
if environ.get("BASE_PATH"):
    BASE_PATH = environ["BASE_PATH"]

## Media on S3 (prod). Active only when S3_MEDIA_BUCKET is set; dev keeps the
## local media volume. No keys configured: boto3's default chain applies, which
## on EC2 means the instance profile role. django-storages[boto3] ships in the
## netbox-docker image.
if environ.get("S3_MEDIA_BUCKET"):
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": environ["S3_MEDIA_BUCKET"],
                "region_name": environ.get("AWS_REGION", "us-east-1"),
                "location": environ.get("S3_MEDIA_PREFIX", "netbox-media"),
                "file_overwrite": False,
            },
        },
    }
