"""Compliance plugin — configuration standards defined in NetBox, checked per device.

The gap this fills. The estate could already answer "is this device running
approved *code*?" (netbox_refresh: SoftwareStandard / DeviceSoftware) and could
already push *config* standards at F5s from a YAML file (scripts/f5). What was
missing was the middle: configuration standards written down in NetBox, with a
per-device verdict recorded against each one, so that "how many devices still
have `ip http server` on?" is a query rather than a script run against the whole
fleet and somebody reading terminal output.

Why a separate plugin rather than more of netbox_refresh. The two are the same
shape — standard vs actual vs compliance, with the same exemption pattern — and
folding them into one Compliance plugin later is a reasonable move. They are
apart today for a purely practical reason: netbox_refresh is under active
development on another work stream, and two streams editing one plugin's
models, migrations and templates collide. The package is named `netbox_compliance`
rather than `netbox_config` precisely so that merge has somewhere to land: the
software half moves in here, and the plugin keeps its name and its URLs.

Model class names are unique across every installed plugin, not just within
one. NetBox builds reverse accessors and content-type lookups from the class
name, and this project has already been bitten once — a `Vendor` clash is why
netbox_quotes has `QuoteVendor`. `SoftwareStandard` is taken, so the models here
are `ConfigStandard` and `ConfigCompliance`.

What lives where. NetBox holds the *definition* of a standard and the *verdict*
per device. It never holds a device's running configuration: several of these
standards match lines that contain secrets, so the running config stays on the
device, and everything stored or printed goes through redaction first. The
checking itself is done by `scripts/ios/ios_standards.py`, which reads the
standards over the REST API, connects to devices over SSH, and posts results
back. That split is deliberate — the pollers that can reach a switch are not the
box NetBox runs on.
"""

from netbox.plugins import PluginConfig


class ComplianceConfig(PluginConfig):
    name = 'netbox_compliance'
    verbose_name = 'Config Compliance'
    description = 'Device configuration standards, per-device compliance and remediation planning'
    version = '0.1.0'
    author = 'Nova Team'
    author_email = 'noreply@example.com'
    base_url = 'compliance'
    min_version = '4.6.0'
    # Pointed at explicitly rather than left to the default ('graphql.schema'),
    # which resolves the attribute `schema` on the PACKAGE
    # netbox_compliance.graphql — where the submodule of the same name shadows
    # the list depending on import order. This form is unambiguous:
    # import_string splits on the last dot, giving module
    # netbox_compliance.graphql.schema, attribute `schema`. Matches
    # netbox_quotes and netbox_refresh.
    graphql_schema = 'graphql.schema.schema'
    default_settings = {
        # A result older than this many days is shown as stale. Freshness is
        # kept separate from the verdict, exactly as netbox_refresh does it: a
        # device can be compliant against a check run three months ago, and the
        # report should say so rather than render it green and confident.
        'stale_after_days': 30,
        # Card placement on the device page.
        'compliance_card_position': 'right_page',
    }


config = ComplianceConfig
