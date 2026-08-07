# SSO test rig: real SAML IdP + real mod_auth_mellon

Rehearse the production SSO setup — the actual SAML round trip, not header
stubs — before touching the prod Apache. Two containers:

- **idp** (`:8083`): SimpleSAMLphp test IdP with static users
- **sp** (`:8082`): Ubuntu Apache + `libapache2-mod-auth-mellon`, running the
  repo's real `apache/netbox.conf` (mounted, transformed at startup: backend
  filled in, the commented "Mellon attributes → identity headers" block
  force-enabled) and proxying to an existing NetBox

This catches the entire class of bug that header-stub tests cannot: Mellon
exports SAML attributes as env vars rather than headers, multi-valued groups
splitting into `MELLON_groups_0/_1` without `MellonMergeEnvVars`, `early` vs
normal phase ordering, `env=` guard behaviour, separator mismatches.

## Run

On a host that already serves NetBox on `:8080` (dev stack or a prod-path
test deployment) — the IdP image is amd64-only:

```bash
cd testing/sso-idp
TEST_HOST=<this host LAN IP> NETBOX_BACKEND=http://172.17.0.1:8080 \
  docker compose up -d --build
```

Browse to **`https://$TEST_HOST:8082/netbox/`** (self-signed certificate —
accept the warning once) — you land on the IdP login form.

| user | password | groups | expectation |
|---|---|---|---|
| `jdoe` | `jdoepass` | netbox-admins, netops | account auto-created, **superuser** via group sync |
| `msmith` | `msmithpass` | netops | account auto-created, NOT superuser |

`groups` is deliberately multi-valued on jdoe: if she arrives with only one
group, multi-value merging is broken. The NetBox behind `NETBOX_BACKEND` needs
the `REMOTE_AUTH_*` block from `env/prod.env.example` active.

## Notes

- Attribute names are exactly prod's (`username`, `email`, `firstName`,
  `lastName`, `memberOf`), so the mapping block runs unmodified. Edit
  `authsources.php` to test other shapes; it is mounted, so
  `docker compose restart idp` applies it.
- The SP is **https on purpose**: this mellon build stamps `SameSite=None` on
  its cookies regardless of `MellonCookieSameSite`, and browsers drop
  None-without-Secure — a plain-http rig fails the cookie test in every real
  browser (bare 400 at `/mellon/postResponse`) while passing scripted tests.
  The IdP stays http; an http page may POST to an https target.
- Wire check while logging in, on the NetBox host:
  `sudo tcpdump -i any -A -s0 'tcp dst port 8080' | grep -iE 'x-remote-user|x-user-'`
- Tear down with `docker compose down --rmi local` in this directory.
