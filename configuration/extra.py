####
## Extra configuration that can't be expressed as plain environment variables.
## Everything here is env-gated so a single file serves dev and prod.
####

from os import environ

## Subpath serving (prod: https://netbox.example.com/netbox, and the dev rehearsal
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

## US date display, everywhere in NetBox.
##
## Harder than it looks, and the two obvious routes are both dead ends. Setting
## DATE_FORMAT here does nothing: Django resolves display formats from the active
## locale's format module *before* it consults settings, and
## django.conf.locale.en.formats defines DATE_FORMAT = 'N j, Y', so the locale
## wins. FORMAT_MODULE_PATH is the documented override, but NetBox copies only a
## fixed list of names out of this file into Django's settings — everything
## except SOCIAL_AUTH_* is dropped — so it never arrives either.
##
## What is left is to give the locale module the values we want. This file is
## imported while settings are being built, long before anything renders a date
## and before Django's format cache has been populated, so the values are simply
## read from here on first use.
from django.conf.locale.en import formats as _en

# Django's `en` default is 'N j, Y' — "Aug. 11, 2026". Wanted numeric US.
_en.DATE_FORMAT = 'm/d/Y'
_en.DATETIME_FORMAT = 'm/d/Y g:i a'
_en.SHORT_DATETIME_FORMAT = 'm/d/Y g:i a'
# SHORT_DATE_FORMAT is already m/d/Y and TIME_FORMAT is already 12-hour.

# What typed and pasted dates are accepted, across all of NetBox rather than
# only the lifecycle importer. US first; ISO kept because it is unambiguous and
# is what the API and every existing export use. Day-first is deliberately
# absent — 03/04/2026 would silently mean a different day.
_en.DATE_INPUT_FORMATS = ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d']
_en.DATETIME_INPUT_FORMATS = [
    '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
]
