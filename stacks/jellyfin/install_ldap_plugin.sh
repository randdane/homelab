#!/usr/bin/env bash
# Install and configure the Jellyfin LDAP Authentication plugin.
#
# The plugin and its config live in the LXC's /config, not in this repo, so without this script a rebuilt Jellyfin silently loses LDAP and
# falls back to local accounts -- which still work, so nothing looks broken.
#
# Run on pve, as root, for a REBUILD only. It rewrites LDAP-Auth.xml whole,
# which discards the library grants (EnabledFolders) and the LdapUsers
# mapping of the live install -- to repoint an existing install, edit
# <LdapServer> alone instead (stacks/jellyfin/README.md). pve has no checkout,
# so hand it authentik's .env:  ENV_FILE=/root/authentik.env ./install_ldap_plugin.sh
# Idempotent.
set -euo pipefail

VERSION="23.0.0.0"
CHECKSUM="e67eda7dd1b91a71315bd6620c8b03f1"   # from repo.jellyfin.org manifest
URL="https://repo.jellyfin.org/files/plugin/ldap-authentication/ldap-authentication_${VERSION}.zip"
ENV_FILE="${ENV_FILE:?set ENV_FILE to a copy of stacks/authentik/.env}"
CT="${CT:-102}"
BASE_DN="dc=ldap,dc=goauthentik,dc=io"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ ! -f "$ENV_FILE" ]; then
    echo "no $ENV_FILE -- need JELLYFIN_LDAP_BIND_PASSWORD" >&2
    exit 1
fi
# authentik publishes LDAP on HOMELAB_HOST, which its .env already holds.
LDAP_HOST="$(grep '^HOMELAB_HOST=' "$ENV_FILE" | cut -d= -f2-)"
: "${LDAP_HOST:?HOMELAB_HOST is empty in $ENV_FILE}"
CT_IP="$(pct exec "$CT" -- hostname -I | awk '{print $1}')"
BIND_PW="$(grep '^JELLYFIN_LDAP_BIND_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
if [ -z "$BIND_PW" ]; then
    echo "JELLYFIN_LDAP_BIND_PASSWORD is empty in $ENV_FILE" >&2
    exit 1
fi

echo "==> downloading plugin $VERSION"
curl -sfL -o "$WORK/ldap.zip" "$URL"
GOT="$(md5sum "$WORK/ldap.zip" | cut -d' ' -f1)"
if [ "$GOT" != "$CHECKSUM" ]; then
    echo "checksum mismatch: expected $CHECKSUM, got $GOT" >&2
    exit 1
fi
echo "    checksum ok"
mkdir -p "$WORK/plug" && unzip -q "$WORK/ldap.zip" -d "$WORK/plug"

# LdapUidAttribute is `cn`, NOT `uid`. authentik's `uid` is a 64-char hash;
# using it names every auto-created Jellyfin account after that hash.
echo "==> writing config"
cat > "$WORK/LDAP-Auth.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<PluginConfiguration xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <LdapUsers />
  <LdapServer>${LDAP_HOST}</LdapServer>
  <LdapPort>3389</LdapPort>
  <UseSsl>false</UseSsl>
  <UseStartTls>false</UseStartTls>
  <SkipSslVerify>false</SkipSslVerify>
  <LdapBindUser>cn=svc-jellyfin-ldap,ou=users,${BASE_DN}</LdapBindUser>
  <LdapBindPassword>${BIND_PW}</LdapBindPassword>
  <LdapBaseDn>ou=users,${BASE_DN}</LdapBaseDn>
  <LdapSearchFilter>(&amp;(objectClass=user)(memberOf=cn=jellyfin-users,ou=groups,${BASE_DN}))</LdapSearchFilter>
  <LdapAdminBaseDn>ou=users,${BASE_DN}</LdapAdminBaseDn>
  <LdapAdminFilter>(ak-superuser=TRUE)</LdapAdminFilter>
  <EnableLdapAdminFilterMemberUid>false</EnableLdapAdminFilterMemberUid>
  <LdapSearchAttributes>cn, sAMAccountName, mail, displayName</LdapSearchAttributes>
  <CreateUsersFromLdap>true</CreateUsersFromLdap>
  <AllowPassChange>false</AllowPassChange>
  <LdapUidAttribute>cn</LdapUidAttribute>
  <LdapUsernameAttribute>cn</LdapUsernameAttribute>
  <LdapPasswordAttribute>userPassword</LdapPasswordAttribute>
  <EnableLdapProfileImageSync>false</EnableLdapProfileImageSync>
  <RemoveImagesNotInLdap>false</RemoveImagesNotInLdap>
  <!-- Deny by default. With EnableAllFolders=true every auto-created user
       gets every library, including ones added later -- so a private library
       added months from now is shared with all family accounts the moment it
       exists, silently. Grant libraries explicitly instead: list their GUIDs
       in EnabledFolders below. GUIDs are per-install, so they cannot be
       committed here; see stacks/jellyfin/README.md for the query. -->
  <EnableAllFolders>false</EnableAllFolders>
  <EnabledFolders />
</PluginConfiguration>
XML

echo "==> installing into CT $CT"
pct exec "$CT" -- systemctl stop jellyfin
pct exec "$CT" -- mkdir -p "/config/plugins/LDAP-Auth_${VERSION}" /config/plugins/configurations
for f in "$WORK"/plug/*.dll "$WORK/plug/meta.json"; do
    pct push "$CT" "$f" "/config/plugins/LDAP-Auth_${VERSION}/$(basename "$f")" --user jellyfin --group jellyfin
done
pct push "$CT" "$WORK/LDAP-Auth.xml" /config/plugins/configurations/LDAP-Auth.xml \
    --user jellyfin --group jellyfin --perms 0600
pct exec "$CT" -- systemctl start jellyfin

echo "==> waiting for jellyfin"
for _ in $(seq 1 30); do
    if curl -sf -m 5 -o /dev/null "http://${CT_IP}:8096/health"; then break; fi
    sleep 2
done
pct exec "$CT" -- journalctl -u jellyfin -o cat | grep -c "Loaded plugin: LDAP-Auth" >/dev/null \
    && echo "    plugin loaded"

echo
echo "Verify with a real login -- a loaded plugin proves nothing:"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' -X POST http://${CT_IP}:8096/Users/AuthenticateByName \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'Authorization: MediaBrowser Client=\"probe\", Device=\"cli\", DeviceId=\"x\", Version=\"1.0\"' \\"
echo "    -d '{\"Username\":\"<ldap-user>\",\"Pw\":\"<password>\"}'"
