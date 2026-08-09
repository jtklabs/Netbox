# NetBox 4.6 API behaviour this scanner relies on

Verified against a live NetBox **4.6.7**. Each item is here because it is
surprising, because getting it wrong fails silently, or both. Several
contradict what we believed when the work started.

Most have a regression test in `tests/test_netbox_live.py` — those run against
a real instance precisely because an in-memory fake returns whatever shape the
test author imagined, which is no check at all on this class of thing.

## Filtering

### `?brief=0` still gives you the brief serializer

NetBox decides brief mode on the parameter being **present**, not on its value.
A brief region carries neither `parent` nor `tags`.

This one shipped as a bug and was caught only by the live tests: ownership
resolution fetched regions with `brief=0`, got objects with no tags and no
parent, and therefore ignored every region-level poller tag. Sites inheriting
their poller from a region — the common case — would never have been scanned.

Send no `brief` parameter at all when you need full objects.

### `?tag=<unknown-slug>` returns 400, not an empty list

```
{"tag": ["Select a valid choice. does-not-exist is not one of the available choices."]}
```

So every tag-filtered query has to be guarded. `/extras/tags/?slug=<slug>`
returns 200 with `count: 0` for a tag that does not exist, which is the safe way
to check first — `NetBox.tag_exists()` wraps it.

This matters more than it looks: a poller whose tag nobody has created yet must
degrade to "no targets", not to a 400 that aborts the scan, and definitely not
to "scan everything".

### Repeated `?tag=` parameters AND together

`?tag=a&tag=b` selects objects carrying **both** tags, not either. There is no
OR form. Ownership is therefore resolved in Python from each object's `tags`
field rather than through a query per tag.

### `?site_id=` on prefixes works — and is broader than `scope_type`

We started from the belief that `/api/ipam/prefixes/?site_id=<id>` returns 400
and that `?scope_type=dcim.site&scope_id=<id>` was required. Both work:

| Filter | Returns |
|---|---|
| `?scope_type=dcim.site&scope_id=7` | prefixes scoped *directly* to that site |
| `?site_id=7` | those, **plus** prefixes scoped to a location inside that site |

`site_id` is what target selection uses, because larger sites model their
prefixes against locations and those prefixes still identify the site.

The 400 that led to the original belief comes from passing an id that does not
exist. NetBox validates filter values against real objects:

```
GET /api/ipam/prefixes/?site_id=999999
400 {"site_id": ["Select a valid choice. 999999 is not one of the available choices."]}
```

`?region_id=` on sites behaves identically, which explains the same confusion
there.

### `?region_id=` on sites includes descendant regions

It is a tree filter. A site in `americas > us-east > nyc` is returned when
filtering on `americas`. Region-level poller tags therefore have to be resolved
by walking *up* from each site to its nearest tagged ancestor, not by expanding
each tagged region downwards — otherwise a sub-region tagged for another poller
could not take its sites back.

### `?contains=` returns every containing prefix, least specific first

```
GET /ipam/prefixes/?contains=10.99.1.55/32
  10.99.0.0/16   (_depth 0)
  10.99.1.0/24   (_depth 1)
```

Taking the first row gives you the aggregate. Sort by mask length descending
and take the longest — `import_ips.py` and `snmp_inventory.py::_site_from_prefix`
both do. Works with or without a `/32` suffix.

## Writes

### MAC addresses are their own model, and creates are not deduplicated

In NetBox 4.x `interface.mac_address` is **read-only**, derived from
`primary_mac_address`. PATCHing it appears to succeed and changes nothing.

The sequence is:

1. `POST /dcim/mac-addresses/` with `assigned_object_type: dcim.interface`
2. `PATCH /dcim/interfaces/<id>/` with `primary_mac_address: <mac id>`

**NetBox does not deduplicate step 1.** Posting the same MAC for the same
interface twice creates two `MACAddress` rows. Without a lookup first, every
rescan of the fleet adds another row per interface. `sync.py::_ensure_mac` looks
up by `?mac_address=&interface_id=` before creating.

### Duplicate creates are 400s with useful messages

```
POST /dcim/manufacturers/  {"name": "Cisco", ...}
400 {"name": ["manufacturer with this name already exists."], ...}

POST /dcim/interfaces/  {"device": 1, "name": "GigabitEthernet1/0/1", ...}
400 {"__all__": ["Interface with this Device and Name already exists."]}
```

Idempotency is therefore lookup-then-create, not create-and-tolerate-failure:
the latter turns every rescan into a wall of 400s that hides the real ones.

### Virtual chassis ordering

A `VirtualChassis` must exist before a device can reference it, but its
`master` is one of those devices — so `master` can only be set afterwards.
Three steps: create the VC, create the members with `virtual_chassis` and
`vc_position`, then PATCH `master`.

`vc_position` and `vc_priority` are readable on `/dcim/devices/` but the nested
member list inside `/dcim/virtual-chassis/<id>/` does **not** include
`vc_position`. Read positions from the devices endpoint.

### Model lookups are case-sensitive

`?model=C9300-48P` matches; `?model=c9300-48p` does not. `?model__ie=` is the
case-insensitive form. Devices report their model consistently, so exact match
is used, but a fleet with mixed-case hand-entered types will produce duplicates.

### Prefix scope serialization

`scope_type` is a content-type string and `scope` is the nested object:

| Scope | `scope_type` | `scope` |
|---|---|---|
| site | `dcim.site` | the site |
| location | `dcim.location` | the location |
| region | `dcim.region` | the region |
| none | `null` | `null` |

There is no `site` field on the prefix serializer.

### There is no per-device software version field

`dcim.Device` in 4.6.7 has no software/firmware version field, and `Platform`
is a shared object (a tree in 4.6, with `parent`), not a per-device version.

The scanner creates a `software_version` text custom field on `dcim.device` and
writes the collected version there, and separately sets `Platform` to the OS
family. Custom fields are created through `/extras/custom-fields/` with
`object_types: ["dcim.device"]`.

`custom_fields` merges on PATCH — sending one key leaves the others alone.

## Authentication

NetBox 4.6 issues v2 tokens shaped `nbt_<key>.<secret>` which authenticate as:

```
Authorization: Bearer nbt_<key>.<secret>
```

Legacy 40-character v1 tokens still use `Authorization: Token <key>`. The
client picks by prefix, so both work.

## Pagination

`?limit=0` returns every result in one response. The client paginates with
explicit `limit`/`offset` anyway, since a `limit=0` against a large instance is
one enormous response and one long-held connection.
