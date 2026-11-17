# Galaxy User Database Reference

This document provides a comprehensive reference for working with Galaxy's user database, including the data model, query functions, API endpoints, and configuration.

---

## 1. User Model/Schema

**File:** `lib/galaxy/model/__init__.py` (line 820)

### Core Fields

```python
class User(Base, Dictifiable, RepresentById):
    __tablename__ = "galaxy_user"

    # Primary Key and Timestamps
    id: Mapped[int]                           # Primary key
    create_time: Mapped[datetime]             # Account creation time
    update_time: Mapped[datetime]             # Last update time

    # Authentication Fields
    email: Mapped[str]                        # Unique, indexed (max 255 chars)
    username: Mapped[Optional[str]]           # Unique, indexed (max 255 chars)
    password: Mapped[str]                     # Hashed password (PBKDF2)
    last_password_change: Mapped[Optional[datetime]]

    # Status Flags
    active: Mapped[bool]                      # Account active (default: True)
    deleted: Mapped[Optional[bool]]           # Soft delete flag (default: False)
    purged: Mapped[Optional[bool]]            # Hard delete flag (default: False)
    external: Mapped[Optional[bool]]          # OIDC/LDAP user (default: False)

    # Account Management
    activation_token: Mapped[Optional[str]]   # Email activation token
    disk_usage: Mapped[Optional[Decimal]]     # Total disk usage in bytes
    preferred_object_store_id: Mapped[str]    # Preferred storage location
```

### Key Relationships

| Relationship | Type | Description |
|--------------|------|-------------|
| `addresses` | List[UserAddress] | User's saved addresses |
| `api_keys` | List[APIKeys] | Non-deleted API keys |
| `roles` | List[UserRoleAssociation] | Role memberships |
| `groups` | List[UserGroupAssociation] | Group memberships |
| `histories` | List[History] | User's workflow histories |
| `quotas` | List[UserQuotaAssociation] | Quota assignments |
| `social_auth` | List[UserAuthnzToken] | OIDC/OAuth tokens |

### Visible Dictionary Keys (API responses)

```python
# Collection (list) view
dict_collection_visible_keys = [
    "id", "email", "username", "deleted", "active", "last_password_change"
]

# Element (detail) view
dict_element_visible_keys = [
    "id", "email", "username", "total_disk_usage", "nice_total_disk_usage",
    "deleted", "active", "last_password_change", "preferred_object_store_id"
]
```

---

## 2. Database Query Functions

**File:** `lib/galaxy/model/db/user.py`

### Available Functions

#### Get User by Email
```python
def get_user_by_email(session, email: str, model_class=User, case_sensitive=True):
    filter_clause = model_class.email == email
    if not case_sensitive:
        filter_clause = func.lower(model_class.email) == func.lower(email)
    stmt = select(model_class).where(filter_clause).limit(1)
    return session.scalars(stmt).first()
```

#### Get User by Username
```python
def get_user_by_username(session, username: str, model_class=User):
    stmt = select(model_class).filter(model_class.username == username).limit(1)
    return session.scalars(stmt).first()
```

#### Get Users by IDs
```python
def get_users_by_ids(session: galaxy_scoped_session, user_ids):
    stmt = select(User).where(User.id.in_(user_ids))
    return session.scalars(stmt).all()
```

#### Search Users with Filters (Most Comprehensive)
```python
def get_users_for_index(
    session,
    deleted: bool,
    f_email: Optional[str] = None,
    f_name: Optional[str] = None,
    f_any: Optional[str] = None,
    is_admin: bool = False,
    expose_user_email: bool = False,
    expose_user_name: bool = False,
):
    stmt = select(User)
    if f_email and (is_admin or expose_user_email):
        stmt = stmt.where(User.email.like(f"%{f_email}%"))
    if f_name and (is_admin or expose_user_name):
        stmt = stmt.where(User.username.like(f"%{f_name}%"))
    if f_any:
        if is_admin:
            stmt = stmt.where(or_(
                User.email.like(f"%{f_any}%"),
                User.username.like(f"%{f_any}%")
            ))
    if deleted:
        stmt = stmt.where(User.deleted == true())
    else:
        stmt = stmt.where(User.deleted == false())
    return session.scalars(stmt).all()
```

---

## 3. API Endpoints

**File:** `lib/galaxy/webapps/galaxy/api/users.py`

### User Management Endpoints

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/api/users` | GET | List all users | Admin for all users |
| `/api/users` | POST | Create new user | Admin only |
| `/api/users/{user_id}` | GET | Get user details | Admin or self |
| `/api/users/{user_id}` | PUT | Update user | Admin or self |
| `/api/users/{user_id}` | DELETE | Delete user | Admin or self |
| `/api/users/deleted` | GET | List deleted users | Admin only |
| `/api/users/deleted/{user_id}` | GET | Get deleted user | Admin only |
| `/api/users/deleted/{user_id}/undelete` | POST | Restore user | Admin only |

### Additional Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/users/{user_id}/api_key` | GET | Get/create API key |
| `/api/users/{user_id}/api_key` | POST | Create new API key |
| `/api/users/{user_id}/api_key` | DELETE | Delete API key |
| `/api/users/{user_id}/roles` | GET | Get user roles (admin) |
| `/api/users/{user_id}/usage` | GET | Get quota usage |
| `/api/users/{user_id}/send_activation_email` | POST | Resend activation |

### Query Parameters for `/api/users`

| Parameter | Type | Description |
|-----------|------|-------------|
| `deleted` | bool | Include deleted users |
| `f_email` | string | Filter by email (partial match) |
| `f_name` | string | Filter by username (partial match) |
| `f_any` | string | Filter by email OR username |

### Example API Usage

```bash
# List all users (requires admin API key)
curl -X GET "http://localhost:8080/api/users" \
  -H "x-api-key: YOUR_ADMIN_API_KEY"

# Search users by email
curl -X GET "http://localhost:8080/api/users?f_email=gmail.com" \
  -H "x-api-key: YOUR_ADMIN_API_KEY"

# Get specific user
curl -X GET "http://localhost:8080/api/users/USER_ID" \
  -H "x-api-key: YOUR_ADMIN_API_KEY"

# Create new user (admin only)
curl -X POST "http://localhost:8080/api/users" \
  -H "x-api-key: YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "new@example.com", "username": "newuser", "password": "secret"}'

# Delete user
curl -X DELETE "http://localhost:8080/api/users/USER_ID" \
  -H "x-api-key: YOUR_ADMIN_API_KEY"
```

---

## 4. Database Configuration

**File:** `config/galaxy.yml` (around line 395)

### Configuration Options

```yaml
galaxy:
  # Database connection string
  # Default: sqlite:///<data_dir>/universe.sqlite?isolation_level=IMMEDIATE
  # PostgreSQL: postgresql://user:password@localhost/galaxy
  #database_connection: null

  # Connection pool settings (for PostgreSQL)
  #database_engine_option_pool_size: 5
  #database_engine_option_max_overflow: 10
  #database_engine_option_pool_recycle: -1

  # Query debugging
  #database_query_profiling_proxy: false
  #slow_query_log_threshold: 0.0

  # Auto-migration
  #database_auto_migrate: false
```

### Default Database Location

```
/path/to/galaxy/database/universe.sqlite
```

---

## 5. Manager and Service Classes

### UserManager Class

**File:** `lib/galaxy/managers/users.py`

```python
class UserManager(base.ModelManager, deletable.PurgableManagerMixin):
    # Key methods:
    def by_id(self, user_id: int) -> Optional[model.User]
    def by_email(self, email: str) -> Optional[model.User]
    def create(self, email=None, username=None, password=None)
    def delete(self, user, flush=True)      # Soft delete
    def purge(self, user, flush=True)       # Hard delete with data removal
    def undelete(self, user)                # Restore deleted user
    def is_admin(self, user) -> bool
    def admins(self) -> List[User]          # Get all admin users
    def change_password(self, trans, password, confirm, token, id, current)
    def get_user_by_identity(self, identity) # By username or email
```

### UsersService Class

**File:** `lib/galaxy/webapps/galaxy/services/users.py`

```python
class UsersService(ServiceBase):
    def get_index(trans, deleted, f_email, f_name, f_any) -> List[UserModel]
    def get_user(trans, user_id) -> UserModel
    def show_user(trans, user_id, deleted) -> UserModel
    def get_user_roles(trans, user_id) -> RoleListResponse
```

---

## 6. Key File Locations Summary

| Component | Path |
|-----------|------|
| User Model Definition | `lib/galaxy/model/__init__.py` (line 820) |
| Database Query Functions | `lib/galaxy/model/db/user.py` |
| API Endpoints | `lib/galaxy/webapps/galaxy/api/users.py` |
| Group User Management | `lib/galaxy/webapps/galaxy/api/group_users.py` |
| UserManager Class | `lib/galaxy/managers/users.py` |
| UsersService Class | `lib/galaxy/webapps/galaxy/services/users.py` |
| Database Configuration | `config/galaxy.yml` (line ~395) |
| Database Initialization | `lib/galaxy/model/mapping.py` |

---

## 7. Direct SQLite Access

For local development, you can query the database directly:

```sql
-- List all users
SELECT id, email, username, create_time, active, deleted
FROM galaxy_user
ORDER BY create_time DESC;

-- Find user by email
SELECT * FROM galaxy_user WHERE email LIKE '%example.com%';

-- Count users by status
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN deleted = 0 OR deleted IS NULL THEN 1 ELSE 0 END) as active,
    SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END) as deleted
FROM galaxy_user;

-- List user roles
SELECT u.email, r.name as role_name
FROM galaxy_user u
JOIN user_role_association ura ON u.id = ura.user_id
JOIN role r ON ura.role_id = r.id;
```

---

## 8. OAuth/OIDC User Linking

### Token Storage Tables

Galaxy uses two tables for OAuth token storage:

**oidc_user_authnz_tokens** (PSA backend - Google, Azure, etc.)
```sql
CREATE TABLE oidc_user_authnz_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES galaxy_user(id),
    uid VARCHAR(255),           -- The identifier the IdP sends: `email` for Google,
                                -- `sub` for generic OIDC/Okta/Keycloak/CILogon/Auth0,
                                -- `preferred_username` for Azure AD V2
    provider VARCHAR(32),       -- The python-social-auth BACKEND name, e.g.
                                -- 'google-openidconnect' -- NOT the `name=` from
                                -- oidc_backends_config.xml. See the table below.
    extra_data JSON,            -- Stores access_token, refresh_token, id_token
    lifetime INTEGER,
    assoc_type VARCHAR(64),
    UNIQUE(provider, uid)
);
```

**custos_authnz_token** — **REMOVED in Galaxy 26.0.0**

Galaxy 26.0.0 ("Reimplement keycloak/cilogon as PSA providers and remove
custos") deleted this backend. Migration
`724237cc4cf0_migrate_custos_to_psa_tokens.py` copies any existing tokens into
`oidc_user_authnz_tokens` — stamped `assoc_type='custos_migrated'` — and then
calls `drop_table(CUSTOS_TABLE)`. Its `downgrade()` recreates and restores it.

On Galaxy >= 26.0 this table does not exist, and `keycloak`/`cilogon` authenticate
through the same PSA path as every other provider. The schema is kept here only
to help interpret instances still running Galaxy < 26.0:

```sql
-- Galaxy < 26.0 ONLY; dropped by migration 724237cc4cf0
CREATE TABLE custos_authnz_token (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES galaxy_user(id),
    external_user_id VARCHAR(255),  -- Sub claim from ID token
    provider VARCHAR(255),          -- custos, cilogon, keycloak
    access_token TEXT,
    id_token TEXT,
    refresh_token TEXT,
    expiration_time DATETIME,
    refresh_expiration_time DATETIME,
    UNIQUE(user_id, external_user_id, provider),
    UNIQUE(external_user_id, provider)
);
```

### How OAuth Linking Works

1. **New OAuth User**: Creates new Galaxy user + token record
2. **Existing User (logged in)**: Creates token record linking to current user
3. **Existing User (not logged in, same email)**: Prompts to login first, then links
4. **Pre-linked User**: Updates tokens on login

### Converting Traditional Users to OAuth

To pre-link an existing user to OAuth:

```sql
-- Create OIDC link for an existing user
INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data, assoc_type)
VALUES (
    1,                          -- existing user ID
    'user@example.com',         -- identifier the IdP will send, lowercased
    'google-openidconnect',     -- the BACKEND name -- 'google' here would never match
    '{}',                       -- tokens populated on first OIDC login
    'prelinked'                 -- marker so the row can be audited/rolled back
);
```

**Provider name translation.** Galaxy stores the backend name, not the
configuration name (`BACKENDS_NAME`, `lib/galaxy/authnz/psa_authnz.py:94`):

| `oidc_backends_config.xml` `name=` | stored in `provider` |
|---|---|
| `google` | `google-openidconnect` |
| `okta` | `okta-openidconnect` |
| `azure` | `azuread-v2-tenant-oauth2` |
| `globus` | `globus` |
| `lifescience` | `life_science` |
| `einfracz` | `e-infra_cz` |
| `nfdi` | `infraproxy` |
| `egi_checkin` | `egi-checkin` |
| `keycloak`, `cilogon`, `auth0`, `oidc`, `tapis`, `elixir` | unchanged |

Galaxy looks the row up with an exact, case-sensitive match on `(provider, uid)`
(`UserAuthnzToken.get_social_auth`, `lib/galaxy/model/__init__.py:11714`), so a
row storing the short name is unreachable. Use
`convert_users_to_oauth.py --verify` to find any.

### Key Files

Verified against Galaxy v26.1.1.

| Component | Path |
|-----------|------|
| UserAuthnzToken model | `lib/galaxy/model/__init__.py:11590` |
| Login-time association lookup | `lib/galaxy/model/__init__.py:11714` (`get_social_auth`) |
| PSA backend + provider mapping | `lib/galaxy/authnz/psa_authnz.py` (`BACKENDS_NAME`, line 94) |
| Account association pipeline step | `lib/galaxy/authnz/psa_authnz.py:985` |
| `fixed_delegated_auth` computation | `lib/galaxy/app/__init__.py:973` |
| OIDC config parsing + XSD validation | `lib/galaxy/authnz/managers.py:125` |
| OIDC backends XSD | `lib/galaxy/authnz/xsd/oidc_backends_config.xsd` |
| Password authentication | `lib/galaxy/auth/__init__.py:74` (`check_password`) |
| Default authenticator set | `lib/galaxy/auth/util.py` (`AUTH_CONF_XML`) |
| Password hashing | `lib/galaxy/security/passwords.py` |
| Custos -> PSA migration | `lib/galaxy/model/migrations/alembic/versions_gxy/724237cc4cf0_migrate_custos_to_psa_tokens.py` |
| Custos backend | removed in 26.0.0 (was `lib/galaxy/authnz/custos_authnz.py`) |

---

## 9. Python Scripts

### search_galaxy_users.py
- Query users via Galaxy API (BioBlend)
- Query users directly from SQLite database
- Filter by email, username, or any field
- Show deleted users
- List user roles

### convert_users_to_oauth.py
- Audit existing links for rows that cannot match at login (`--verify`)
- List users with/without OIDC links, showing stored backend names
- Preview conversion (dry run)
- Pre-link users, translating provider names correctly
- Retire credentials: `--mark-external`, `--invalidate-password`, `--rotate-api-keys`
- Remove links, or roll back every row the tool created (`--remove-all-prelinked`)

### galaxy_db.py
- Shared database layer for SQLite and PostgreSQL
- Provider name <-> backend name translation
- Galaxy-compatible PBKDF2 password hashing

### test_convert_users_to_oauth.py
- Regression tests, stdlib only: `python3 test_convert_users_to_oauth.py`
