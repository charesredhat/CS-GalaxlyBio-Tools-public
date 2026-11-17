#!/bin/bash
# Galaxy OIDC Implementation Setup Script
# This script prepares the Galaxy instance for OIDC authentication

set -e

# Configure these paths for your environment
GALAXY_DIR="${GALAXY_DIR:-$HOME/galaxy}"
CONFIG_DIR="$GALAXY_DIR/config"

echo "🌌 Galaxy OIDC Setup Script"
echo "=============================="
echo "Galaxy Directory: $GALAXY_DIR"
echo "Config Directory: $CONFIG_DIR"
echo ""

# Check if Galaxy directory exists
if [ ! -d "$GALAXY_DIR" ]; then
    echo "❌ Galaxy directory not found: $GALAXY_DIR"
    exit 1
fi

# Navigate to config directory
cd "$CONFIG_DIR"

# Make sure a galaxy.yml exists, WITHOUT destroying one that already does.
#
# This used to `cp galaxy.yml.sample galaxy.yml` unconditionally, which threw
# away the live configuration on every run -- and because the copy happened
# before the merge step below, that step could never see the operator's real
# settings, making its "leave existing values alone" logic unreachable. Re-running
# also overwrote galaxy.yml.backup with the sample-derived file, destroying the
# only copy of the original.
if [ -L "galaxy.yml" ]; then
    # A symlink (a stock Galaxy checkout links galaxy.yml -> the sample). Replace
    # it with a real file so we are never editing the shipped sample in place.
    echo "📝 galaxy.yml is a symlink; replacing it with a real copy of the sample"
    rm galaxy.yml
    cp ../lib/galaxy/config/sample/galaxy.yml.sample galaxy.yml
elif [ ! -f "galaxy.yml" ]; then
    echo "📝 No galaxy.yml found; creating one from the sample"
    cp ../lib/galaxy/config/sample/galaxy.yml.sample galaxy.yml
else
    # Back up the real configuration before touching it, without clobbering an
    # earlier backup.
    backup="galaxy.yml.backup"
    if [ -e "$backup" ]; then
        backup="galaxy.yml.backup.$(date +%Y%m%d%H%M%S)"
    fi
    echo "📁 Backing up existing galaxy.yml to $backup"
    cp galaxy.yml "$backup"
    echo "📝 Editing the existing galaxy.yml in place (settings already present are left alone)"
fi

# Add OIDC configuration to galaxy.yml
#
# This MUST merge into the existing `galaxy:` mapping rather than appending a
# second one. galaxy.yml.sample already defines `galaxy:` as a top-level key,
# and YAML keeps only the LAST occurrence of a duplicate key -- so appending a
# second `galaxy:` block silently discards every setting the admin uncommented
# in the sample. Verified: yaml.safe_load("galaxy:\n  a: 1\ngalaxy:\n  c: 3\n")
# returns {'galaxy': {'c': 3}}.
#
# Inserting the keys textually under the existing mapping (rather than
# round-tripping through a YAML dumper) keeps the sample's comments intact.
echo "Enabling OIDC in Galaxy configuration..."
python3 - "galaxy.yml" << 'GALAXY_YML_PATCH'
import re
import sys

config_path = sys.argv[1]
BEGIN = "  # ===== BEGIN OIDC configuration (managed by setup_galaxy_oidc.sh) ====="
END = "  # ===== END OIDC configuration ====="

# Settings we actively set, and commented-out suggestions we always include.
SETTINGS = [
    ("enable_oidc", "true", ["  # Enable OpenID Connect support"]),
    ("oidc_config_file", "oidc_config.xml",
     ["  # OIDC configuration files (relative to this config directory)"]),
    ("oidc_backends_config_file", "oidc_backends_config.xml", []),
    ("admin_users", '"admin@example.com"', ["  # Admin users (replace with your admin emails)"]),
    ("allow_local_account_creation", "true", [
        "  # Allow unregistered users to create LOCAL (password) accounts.",
        "  # Renamed in Galaxy 26.1; `allow_user_creation` is a deprecated alias.",
        "  # Set to false once you cut over to SSO.",
    ]),
]

SUGGESTIONS = [
    "",
    "  # Put the OIDC button first on the login page.",
    "  # prefer_oidc_login: true",
    "",
    "  # Turn off local accounts at the UI/registration level. NOTE: this does",
    "  # NOT stop /api/authenticate/baseauth from accepting an existing password",
    "  # -- see auth_conf.xml.sso-template, which is the lever that does.",
    "  # disable_local_accounts: true",
    "",
    "  # Optional: require login for all access",
    "  # require_login: true",
]

with open(config_path) as handle:
    text = handle.read()

# Drop our own previous block first, so re-running is idempotent and so the
# already-set detection below doesn't see our own keys as the admin's.
existing = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
text = existing.sub("", text)

# Which keys does the galaxy: mapping already define? Writing a key that is
# already set would create a duplicate mapping key -- legal YAML, but the last
# occurrence silently wins, which is exactly the failure mode this block exists
# to avoid. Leave the admin's value alone and say so instead.
already_set = set()
try:
    import yaml
except ImportError:
    yaml = None
else:
    section = (yaml.safe_load(text) or {}).get("galaxy") or {}
    already_set = set(section)

lines = [BEGIN]
skipped = []
for key, value, comments in SETTINGS:
    if key in already_set:
        skipped.append(key)
        lines.append(f"  # {key}: left at the value already set in this file")
        continue
    lines.extend(comments)
    lines.append(f"  {key}: {value}")
    lines.append("")
lines.extend(SUGGESTIONS)
lines.append(END)
block = "\n".join(lines)

match = re.search(r"^galaxy:[ \t]*$", text, re.M)
if match:
    text = text[:match.end()] + "\n" + block + "\n" + text[match.end():]
else:
    text = text.rstrip("\n") + "\n\ngalaxy:\n" + block + "\n"

with open(config_path, "w") as handle:
    handle.write(text)

if skipped:
    print(f"   Left existing values untouched for: {', '.join(skipped)}")

# Fail loudly rather than leaving behind a config Galaxy will reject.
if yaml is None:
    print("   (PyYAML not installed; skipping verification of galaxy.yml)")
else:
    with open(config_path) as handle:
        parsed = yaml.safe_load(handle) or {}
    section = parsed.get("galaxy") or {}
    missing = [k for k in ("enable_oidc", "oidc_backends_config_file") if k not in section]
    if missing:
        sys.exit(f"ERROR: galaxy.yml did not come out as expected; missing {missing}")
    top_level = len(re.findall(r"^galaxy:[ \t]*$", open(config_path).read(), re.M))
    if top_level != 1:
        sys.exit(f"ERROR: expected exactly one top-level 'galaxy:' key, found {top_level}")
    print(f"   Verified: galaxy.yml parses; one galaxy: mapping with {len(section)} key(s)")
GALAXY_YML_PATCH

# Create OIDC general configuration
echo "⚙️ Creating OIDC general configuration..."
cat > oidc_config.xml << 'EOF'
<?xml version="1.0"?>
<!--
OIDC General Configuration
This file contains general OIDC settings for Galaxy.
-->
<OIDC>
    <!-- SSL verification. Keep this True: with False, Galaxy will not verify the
         IdP's certificate, so a network attacker can impersonate the identity
         provider. Only set it to False against a local IdP using a self-signed
         certificate, and never on an internet-facing instance. -->
    <Setter Property="VERIFY_SSL" Value="True" Type="bool"/>
    
    <!-- Request timeout in seconds -->
    <Setter Property="REQUESTS_TIMEOUT" Value="30" Type="float"/>
    
    <!-- ID token maximum age in seconds -->
    <Setter Property="ID_TOKEN_MAX_AGE" Value="3600" Type="float"/>
</OIDC>
EOF

# Create OIDC backends configuration template
echo "🔐 Creating OIDC backends configuration template..."
cat > oidc_backends_config.xml << 'EOF'
<?xml version="1.0"?>
<!--
OIDC Backends Configuration
Configure your OIDC providers here. Examples are provided for common providers.
Uncomment and configure the provider you want to use.
-->
<OIDC>
    <!-- Google OIDC Provider -->
    <!-- 
    <provider name="google">
        <client_id>YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com</client_id>
        <client_secret>YOUR_GOOGLE_CLIENT_SECRET</client_secret>
        <redirect_uri>http://127.0.0.1:8080/authnz/google/callback</redirect_uri>
        <prompt>consent</prompt>
        <icon>https://developers.google.com/static/identity/images/branding_guideline_sample_lt_sq_lg.svg</icon>
    </provider>
    -->
    
    <!-- Microsoft Azure/Office365 Provider -->
    <!-- 
    <provider name="azure">
        <client_id>YOUR_AZURE_CLIENT_ID</client_id>
        <client_secret>YOUR_AZURE_CLIENT_SECRET</client_secret>
        <redirect_uri>http://127.0.0.1:8080/authnz/azure/callback</redirect_uri>
        <tenant_id>YOUR_AZURE_TENANT_ID</tenant_id>
    </provider>
    -->
    
    <!-- Keycloak Provider -->
    <!-- 
    <provider name="keycloak">
        <url>https://your-keycloak.example.com/realms/master/</url>
        <client_id>galaxy</client_id>
        <client_secret>YOUR_KEYCLOAK_CLIENT_SECRET</client_secret>
        <redirect_uri>http://127.0.0.1:8080/authnz/keycloak/callback</redirect_uri>
        <label>Company SSO</label>
    </provider>
    -->
    
    <!-- Generic OIDC Provider -->
    <!-- 
    <provider name="oidc">
        <client_id>YOUR_CLIENT_ID</client_id>
        <client_secret>YOUR_CLIENT_SECRET</client_secret>
        <redirect_uri>http://127.0.0.1:8080/authnz/oidc/callback</redirect_uri>
        <oidc_endpoint>https://your-provider.com/auth/realms/galaxy</oidc_endpoint>
        <custom_button_text>Company Login</custom_button_text>
    </provider>
    -->
</OIDC>
EOF

# Both files are gitignored because they hold provider credentials, but the
# gitignore only protects the repo -- not the disk. Written at the default
# umask they typically land 0644, readable by every account on a shared Galaxy
# host, so restrict them to the owner now rather than after secrets go in.
chmod 600 oidc_config.xml oidc_backends_config.xml

# Write an auth_conf.xml template for the eventual SSO cutover.
#
# This file is what actually controls password authentication, and it is the
# only lever that closes it off completely:
#
#   * The DEFAULT (no auth_conf.xml) loads a single `localdb` authenticator --
#     see AUTH_CONF_XML in lib/galaxy/auth/util.py.
#   * An empty <auth></auth> loads ZERO authenticators, so
#     AuthManager.check_password() iterates nothing and always returns False.
#     That kills password login everywhere, including /api/authenticate/baseauth,
#     which neither `external` nor `disable_local_accounts` does.
#   * Zero authenticators is also half of what turns on fixed_delegated_auth:
#       lib/galaxy/app/__init__.py:973
#       fixed_delegated_auth = (len(config.oidc) == 1 and len(authenticators) == 0)
#     With exactly one OIDC provider configured, Galaxy then auto-associates an
#     IdP identity with an existing account by email on first login -- which is
#     the supported alternative to bulk DB pre-linking.
#
# It is written as a .sso-template rather than activated, because enabling it
# locks out every user who cannot yet authenticate against the IdP.
echo "Creating auth_conf.xml.sso-template (NOT active)..."
cat > auth_conf.xml.sso-template << 'EOF'
<?xml version="1.0"?>
<!--
SSO cutover template -- NOT ACTIVE UNTIL YOU RENAME IT TO auth_conf.xml
AND set `auth_config_file: auth_conf.xml` in galaxy.yml.

Zero authenticators means no password can ever authenticate, on the web form
or via /api/authenticate/baseauth. Combined with exactly one configured OIDC
provider it also enables fixed_delegated_auth, so existing accounts are
auto-associated with their IdP identity by email on first login.

BEFORE ACTIVATING:
  1. Confirm every active user can authenticate at the IdP with the SAME email
     address recorded in galaxy_user. Run:
       python3 search-and-edit-gal/convert_users_to_oauth.py --verify ...
  2. Make sure at least one admin can log in via the IdP, or you will lock
     yourself out of the admin panel.
  3. Rotate API keys -- they bypass password auth entirely and keep working:
       python3 search-and-edit-gal/convert_users_to_oauth.py --convert-all            --provider <name> --rotate-api-keys
-->
<auth>
</auth>
EOF

echo "✅ OIDC configuration files created successfully!"
echo ""
echo "📋 Next Steps:"
echo "1. Choose an OIDC provider (Google, Azure, Keycloak, etc.)"
echo "2. Register your Galaxy instance with the provider"
echo "3. Edit oidc_backends_config.xml with your provider credentials"
echo "4. Restart Galaxy to apply changes"
echo ""
echo "📁 Configuration files created:"
echo "   - galaxy.yml (OIDC enabled)"
echo "   - oidc_config.xml (general settings)"
echo "   - oidc_backends_config.xml (provider settings - needs editing)"
echo "   - auth_conf.xml.sso-template (inactive; activate to retire password login)"
echo ""
echo "🔧 To configure a provider, edit:"
echo "   $CONFIG_DIR/oidc_backends_config.xml"
echo ""
echo "🚀 To restart Galaxy:"
echo "   cd $GALAXY_DIR"
echo "   ./run.sh --stop"
echo "   ./run.sh"
echo ""
echo "📖 For detailed instructions, see search-and-edit-gal/OAUTH_CONVERSION_GUIDE.md"