#!/bin/bash
# Galaxy OIDC Provider Configuration Helper
# This script helps configure specific OIDC providers

set -e

# Configure this path for your environment
CONFIG_DIR="${GALAXY_CONFIG_DIR:-$HOME/galaxy/config}"
CONFIG_FILE="$CONFIG_DIR/oidc_backends_config.xml"

# Base URL that a *browser* is redirected back to after the IdP's consent
# screen. It must match what the user types in their address bar, not what
# Galaxy binds locally: on a public instance the browser is on someone's
# laptop, where 127.0.0.1:8080 is not Galaxy at all. Real IdPs also refuse
# plain-http redirect URIs for anything but localhost, so a public instance has
# to register its https name here and in the provider's console. Defaults to
# the old hardcoded loopback value so a local-only setup behaves as before.
PUBLIC_URL="${GALAXY_PUBLIC_URL:-http://127.0.0.1:8080}"
PUBLIC_URL="${PUBLIC_URL%/}"

# Merge a single <provider> XML fragment into CONFIG_FILE, replacing any
# existing provider with the same `name` attribute and leaving every other
# already-configured provider untouched. (Configuring a second provider used
# to `cat >` the whole file, destroying any provider configured earlier.)
merge_provider_xml() {
    local fragment_file="$1"
    python3 - "$CONFIG_FILE" "$fragment_file" <<'PYEOF'
import os
import sys
import xml.dom.minidom as minidom

config_path, fragment_path = sys.argv[1], sys.argv[2]

# minidom models the whole document (not just the root element), so comments
# that appear *before* <OIDC> -- like the instructional header block
# setup_galaxy_oidc.sh writes into a fresh config -- survive the merge too.
# (xml.etree.ElementTree can only preserve comments that are children of the
# root, not siblings of it, so an earlier version of this script using it
# still silently dropped that leading comment block.)
doc = minidom.parse(config_path)
root = doc.documentElement

fragment_doc = minidom.parse(fragment_path)
new_provider = fragment_doc.documentElement
new_name = (new_provider.getAttribute("name") or "").lower()

# Case-insensitive match: older configs (and setup_galaxy_oidc.sh's template)
# used capitalized names like "Google"/"Keycloak" before this script started
# writing lowercase names, so an exact-case comparison would fail to replace
# them and leave a duplicate, conflicting <provider> entry behind.
for existing in list(root.getElementsByTagName("provider")):
    if (existing.getAttribute("name") or "").lower() == new_name:
        root.removeChild(existing)

root.appendChild(doc.createTextNode("    "))
root.appendChild(doc.importNode(new_provider, deep=True))
root.appendChild(doc.createTextNode("\n"))

with open(config_path, "w", encoding="utf-8") as f:
    f.write(doc.toxml())

# This file now holds OIDC client secrets. Written at the default umask it
# would typically land 0644 -- readable by every account on a shared Galaxy
# host. Narrow it to the owner. (Done after the write, so it also corrects a
# file an earlier run or setup_galaxy_oidc.sh created too permissively.)
os.chmod(config_path, 0o600)
PYEOF
}

# Escape XML metacharacters in free-text input before embedding it into a
# provider fragment's element text content. Without this, a value containing
# e.g. "&" (a plausible Display Label like "R&D SSO") produces a fragment
# that isn't well-formed XML, and merge_provider_xml's minidom.parse() raises
# instead of saving the config.
xml_escape() {
    local s="$1"
    s="${s//&/&amp;}"
    s="${s//</&lt;}"
    s="${s//>/&gt;}"
    printf '%s' "$s"
}

# Read a secret without echoing it. Client secrets typed at a `read -rp` prompt
# stay visible in the terminal scrollback, which on an SSH session is the whole
# exposure surface; -s keeps them off the screen entirely.
read_secret() {
    local __var="$1" __prompt="$2"
    read -rsp "$__prompt" "$__var"
    echo ""  # -s also swallows the user's newline; restore it so output stays aligned
}

echo "🔐 Galaxy OIDC Provider Configuration Helper"
echo "=============================================="
echo ""

# Check if OIDC is already set up
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ OIDC not set up yet. Run setup_galaxy_oidc.sh first."
    exit 1
fi

echo "Choose OIDC provider to configure:"
echo "1) Google"
echo "2) Microsoft Azure/Office365"
echo "3) Keycloak"
echo "4) Auth0"
echo "5) Generic OIDC"
echo "6) View current configuration"
echo "7) Exit"
echo ""

read -rp "Enter choice (1-7): " choice

# Every provider branch below reads its credentials into the same two
# variables, $client_id and $client_secret. That keeps merge_provider_xml and
# xml_escape generic, but it means `grep "Client ID"` returns five
# indistinguishable hits and it is easy to edit or read the wrong branch. Each
# prompt is therefore tagged with the menu option it belongs to.
case $choice in
    1)
        echo ""
        echo "🌟 Configuring Google OIDC"
        echo "=========================="
        echo ""
        echo "📋 Prerequisites:"
        echo "1. Go to Google Cloud Console: https://console.cloud.google.com/"
        echo "2. Create/select a project"
        echo "3. Enable Google+ API"
        echo "4. Create OAuth 2.0 Client ID"
        echo "5. Set authorized redirect URI: $PUBLIC_URL/authnz/google/callback"
        echo ""

        read -rp "Enter Google Client ID: " client_id  # menu option 1 (Google)
        read_secret client_secret "Enter Google Client Secret: "
        client_id="$(xml_escape "$client_id")"
        client_secret="$(xml_escape "$client_secret")"

        fragment_file="$(mktemp)"
        trap 'rm -f "$fragment_file"' EXIT
        cat > "$fragment_file" << EOF
<provider name="google">
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/google/callback</redirect_uri>
    <prompt>consent</prompt>
    <icon>https://developers.google.com/static/identity/images/branding_guideline_sample_lt_sq_lg.svg</icon>
</provider>
EOF
        merge_provider_xml "$fragment_file"
        echo "✅ Google OIDC configured successfully!"
        ;;

    2)
        echo ""
        echo "🏢 Configuring Microsoft Azure OIDC"
        echo "==================================="
        echo ""
        echo "📋 Prerequisites:"
        echo "1. Go to Azure Portal: https://portal.azure.com/"
        echo "2. Azure Active Directory → App registrations → New registration"
        echo "3. Set redirect URI: $PUBLIC_URL/authnz/azure/callback"
        echo "4. Create client secret in 'Certificates & secrets'"
        echo ""

        read -rp "Enter Azure Client ID: " client_id  # menu option 2 (Azure)
        read_secret client_secret "Enter Azure Client Secret: "
        read -rp "Enter Azure Tenant ID: " tenant_id
        client_id="$(xml_escape "$client_id")"
        client_secret="$(xml_escape "$client_secret")"
        tenant_id="$(xml_escape "$tenant_id")"

        fragment_file="$(mktemp)"
        trap 'rm -f "$fragment_file"' EXIT
        cat > "$fragment_file" << EOF
<provider name="azure">
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/azure/callback</redirect_uri>
    <tenant_id>$tenant_id</tenant_id>
</provider>
EOF
        merge_provider_xml "$fragment_file"
        echo "✅ Azure OIDC configured successfully!"
        ;;

    3)
        echo ""
        echo "🔐 Configuring Keycloak OIDC"
        echo "============================="
        echo ""
        echo "📋 Prerequisites:"
        echo "1. Have Keycloak server running"
        echo "2. Create a client in Keycloak admin console"
        echo "3. Set valid redirect URI: $PUBLIC_URL/authnz/keycloak/callback"
        echo ""

        read -rp "Enter Keycloak Server URL (e.g., https://keycloak.example.com/realms/master/): " keycloak_url
        read -rp "Enter Keycloak Client ID: " client_id  # menu option 3 (Keycloak)
        read_secret client_secret "Enter Keycloak Client Secret: "
        read -rp "Enter Display Label (e.g., Company SSO): " label
        # The config element is <idphint>. It is NOT called <kc_idp_hint>:
        # lib/galaxy/authnz/xsd/oidc_backends_config.xsd declares "idphint",
        # psa_authnz.py reads oidc_backend_config.get("idphint") into IDPHINT,
        # and KeycloakOpenIdConnect.auth_params() is what turns that into the
        # kc_idp_hint *query parameter* sent to Keycloak (defaulting to "oidc").
        #
        # Getting this wrong is fatal, not a no-op: AuthnzManager parses this
        # file with parse_xml(..., schemafname=OIDC_BACKEND_SCHEMA), which calls
        # schema.assertValid(). The provider content model is <xs:all>, so an
        # undeclared child element makes Galaxy refuse to start.
        read -rp "Enter Keycloak idphint (optional, e.g. company-saml; preselects an upstream IdP): " idphint
        keycloak_url="$(xml_escape "$keycloak_url")"
        client_id="$(xml_escape "$client_id")"
        client_secret="$(xml_escape "$client_secret")"
        label="$(xml_escape "$label")"
        idphint="$(xml_escape "$idphint")"

        fragment_file="$(mktemp)"
        trap 'rm -f "$fragment_file"' EXIT
        if [ -n "$idphint" ]; then
            cat > "$fragment_file" << EOF
<provider name="keycloak">
    <url>$keycloak_url</url>
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/keycloak/callback</redirect_uri>
    <label>$label</label>
    <idphint>$idphint</idphint>
</provider>
EOF
        else
            cat > "$fragment_file" << EOF
<provider name="keycloak">
    <url>$keycloak_url</url>
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/keycloak/callback</redirect_uri>
    <label>$label</label>
</provider>
EOF
        fi
        merge_provider_xml "$fragment_file"
        echo "✅ Keycloak OIDC configured successfully!"
        ;;

    4)
        echo ""
        echo "🌐 Configuring Auth0 OIDC"
        echo "=========================="
        echo ""
        echo "📋 Prerequisites:"
        echo "1. Go to the Auth0 Dashboard: https://manage.auth0.com/"
        echo "2. Applications → Create Application (Regular Web Application)"
        echo "3. Set Allowed Callback URL: $PUBLIC_URL/authnz/auth0/callback"
        echo "4. Note your Auth0 domain (e.g., your-tenant.us.auth0.com)"
        echo ""
        echo "ℹ️  Requires Galaxy >= 26.1.0 (improved Auth0 OIDC support)."
        echo ""

        # Auth0 resolves its OIDC endpoint from <domain>, never from <url>:
        # GalaxyAuth0OpenIdConnect inherits Auth0OpenIdConnectAuth.oidc_endpoint()
        # -> api_path() -> normalized_domain() -> setting("DOMAIN"), which raises
        # AuthMissingParameter(DOMAIN) when unset. <url> is schema-valid, so
        # using it fails at first login rather than at startup. Give the bare
        # host with no scheme, as the XSD documents for this element.
        read -rp "Enter Auth0 Domain (e.g., your-tenant.us.auth0.com): " auth0_domain
        read -rp "Enter Auth0 Client ID: " client_id  # menu option 4 (Auth0)
        read_secret client_secret "Enter Auth0 Client Secret: "
        auth0_domain="$(xml_escape "$auth0_domain")"
        client_id="$(xml_escape "$client_id")"
        client_secret="$(xml_escape "$client_secret")"

        fragment_file="$(mktemp)"
        trap 'rm -f "$fragment_file"' EXIT
        cat > "$fragment_file" << EOF
<provider name="auth0">
    <domain>$auth0_domain</domain>
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/auth0/callback</redirect_uri>
</provider>
EOF
        merge_provider_xml "$fragment_file"
        echo "✅ Auth0 OIDC configured successfully!"
        ;;

    5)
        echo ""
        echo "🔧 Configuring Generic OIDC Provider"
        echo "====================================="
        echo ""
        echo "📋 Prerequisites:"
        echo "1. Have OIDC provider with .well-known/openid-configuration endpoint"
        echo "2. Register Galaxy as client with provider"
        echo "3. Set valid redirect URI: $PUBLIC_URL/authnz/oidc/callback"
        echo ""

        read -rp "Enter OIDC Endpoint (without /.well-known/openid-configuration): " oidc_endpoint
        read -rp "Enter Generic OIDC Client ID: " client_id  # menu option 5 (generic OIDC)
        read_secret client_secret "Enter Client Secret: "
        read -rp "Enter Custom Button Text (e.g., Company Login): " button_text
        oidc_endpoint="$(xml_escape "$oidc_endpoint")"
        client_id="$(xml_escape "$client_id")"
        client_secret="$(xml_escape "$client_secret")"
        button_text="$(xml_escape "$button_text")"

        fragment_file="$(mktemp)"
        trap 'rm -f "$fragment_file"' EXIT
        cat > "$fragment_file" << EOF
<provider name="oidc">
    <client_id>$client_id</client_id>
    <client_secret>$client_secret</client_secret>
    <redirect_uri>$PUBLIC_URL/authnz/oidc/callback</redirect_uri>
    <oidc_endpoint>$oidc_endpoint</oidc_endpoint>
    <custom_button_text>$button_text</custom_button_text>
</provider>
EOF
        merge_provider_xml "$fragment_file"
        echo "✅ Generic OIDC configured successfully!"
        ;;

    6)
        echo ""
        echo "📄 Current OIDC Configuration"
        echo "=============================="
        echo ""
        if [ -f "$CONFIG_FILE" ]; then
            cat "$CONFIG_FILE"
        else
            echo "No OIDC backends configuration found."
        fi
        ;;

    7)
        echo "👋 Goodbye!"
        exit 0
        ;;

    *)
        echo "❌ Invalid choice. Please run the script again."
        exit 1
        ;;
esac

echo ""
echo "🚀 Next Steps:"
echo "1. Restart Galaxy to apply changes:"
echo "   cd \$GALAXY_DIR"
echo "   ./run.sh --stop"
echo "   ./run.sh"
echo ""
echo "2. Test the configuration:"
echo "   - Visit http://127.0.0.1:8080/"
echo "   - Look for the OIDC login button"
echo "   - Try logging in with your provider"
echo ""
echo "3. Monitor Galaxy logs for any issues:"
echo "   tail -f \$GALAXY_DIR/galaxy.log"
