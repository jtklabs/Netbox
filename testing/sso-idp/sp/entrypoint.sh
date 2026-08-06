#!/bin/bash
set -e

# Build the conf under test from the mounted repo file:
#  1. real backend address,
#  2. force-enable the commented "Mellon attributes -> identity headers" block,
#  3. this IdP names its username attribute "username" (like prod's
#     MELLON_username), while the repo example says MELLON_uid.
sed -e "s|^Define NETBOX_BACKEND .*|Define NETBOX_BACKEND ${NETBOX_BACKEND}|" \
    -e '/^# <Location \/netbox>/,/^# <\/Location>/ s/^# \{0,1\}//' \
    -e 's/MELLON_uid/MELLON_username/g' \
    /opt/netbox.conf.src > /etc/apache2/conf-available/netbox.conf
grep -q '^<Location /netbox>' /etc/apache2/conf-available/netbox.conf \
  || { echo "FATAL: mapping block not found/uncommented — did apache/netbox.conf change shape?"; exit 1; }
sed -i "s/TEST_HOST/${TEST_HOST}/g" /etc/apache2/conf-available/mellon-base.conf /etc/apache2/sites-available/000-default.conf

mkdir -p /etc/apache2/mellon
if [ ! -f /etc/apache2/mellon/sp-key.pem ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=netbox-ssotest-sp" \
    -keyout /etc/apache2/mellon/sp-key.pem -out /etc/apache2/mellon/sp-cert.pem 2>/dev/null
fi

# Metadata must be fetched via the PUBLISHED URL: the endpoints inside it are
# built from the Host header, and the browser has to be able to reach them.
echo "fetching IdP metadata from $IDP_METADATA_URL"
ok=""
for i in $(seq 1 60); do
  if curl -fsS -o /etc/apache2/mellon/idp-metadata.xml "$IDP_METADATA_URL"; then ok=1; break; fi
  sleep 2
done
[ -n "$ok" ] && [ -s /etc/apache2/mellon/idp-metadata.xml ] || { echo "FATAL: could not fetch IdP metadata"; exit 1; }

apache2ctl configtest
exec apache2ctl -DFOREGROUND
