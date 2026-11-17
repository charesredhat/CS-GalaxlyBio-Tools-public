# How to Use search_galaxy_users.py

Search Galaxy users either through the Galaxy API or directly from the SQLite database.

---

## Requirements

- Python 3
- `bioblend` (API method only): `pip install bioblend`
- Access to a running Galaxy instance (API method) or the Galaxy database file (DB method)

---

## Environment Variables

Set these to avoid passing flags every time:

```bash
export GALAXY_URL="http://localhost:8080"
export GALAXY_API_KEY="your_admin_api_key"
export GALAXY_DB_PATH="/path/to/galaxy/database/universe.sqlite"
```

---

## Methods

The script has two ways to search users:

| Method | When to use |
|--------|-------------|
| `--method db` | Galaxy is offline or you have direct DB access (default) |
| `--method api` | Galaxy is running and you have an admin API key |

---

## Usage

### List all users (database)

```bash
python search_galaxy_users.py --method db
```

### List all users (API)

```bash
python search_galaxy_users.py --method api --api-key YOUR_ADMIN_KEY
```

Each user entry shows all available metadata:

| Field | Description |
| ----- | ----------- |
| Auth Type | `OAuth/OIDC` or `Traditional` |
| External Flag | Whether the account uses external auth |
| Active / Deleted / Purged | Account status flags |
| Last Password Change | Date of last password update |
| Disk Usage | Total storage used (human-readable) |
| Preferred Object Store | Configured storage backend |

A summary line at the end shows total, OAuth, and traditional counts.

> The API method fetches full user detail per user to retrieve all metadata. On large instances this may be slow — use `--method db` for faster bulk queries.

---

## Filtering Users

### Search by email (partial match)

```bash
python search_galaxy_users.py --method db --email gmail.com
python search_galaxy_users.py --method db --email user@example.com
```

### Search by username (partial match)

```bash
python search_galaxy_users.py --method db --username admin
```

### Combine filters

```bash
python search_galaxy_users.py --method db --email example.com --username john
```

---

## Show Deleted Users

By default deleted users are hidden. Use `--show-deleted` to include them:

```bash
python search_galaxy_users.py --method db --show-deleted
```

---

## Show All Fields

The `--all-fields` flag adds extra columns: update time, purged status, disk usage, and external auth flag. DB method only.

```bash
python search_galaxy_users.py --method db --all-fields
```

---

## List Users by Authentication Type

Shows every user with their auth type (OAuth/OIDC or Traditional), plus all metadata columns from the database.

### All users with auth type

```bash
python search_galaxy_users.py --auth-type all
```

### Only OAuth/OIDC users

```bash
python search_galaxy_users.py --auth-type oauth
```

### Only traditional (local password) users

```bash
python search_galaxy_users.py --auth-type local
```

Each user entry shows:

| Field | Description |
| ----- | ----------- |
| Auth Type | `OAuth/OIDC` or `Traditional` |
| External Flag | Raw `external` column from `galaxy_user` |
| Active / Deleted / Purged | Account status flags |
| Created / Last Updated | Timestamps |
| Last Password Change | Date of last password update |
| Disk Usage | Storage used in bytes |
| Preferred Object Store | Configured storage backend |

> OAuth detection reads `oidc_user_authnz_tokens` plus the `external` flag on the
> user record, and reports which provider each user is linked to.
>
> It also probes `custos_authnz_token`, but only for instances still on Galaxy
> < 26.0: Galaxy 26.0.0 migrated those tokens into `oidc_user_authnz_tokens` and
> **dropped** that table (migration `724237cc4cf0`). Its absence is normal and is
> not an error. If the probe does find it, the script prints a note saying the
> instance predates 26.0.

---

## List User Roles

### All user-role assignments

```bash
python search_galaxy_users.py --roles
```

### Roles for a specific user

```bash
python search_galaxy_users.py --roles --user-id 3
```

> `--roles` always uses the database regardless of `--method`.

---

## Custom Database Path

```bash
python search_galaxy_users.py --method db --db-path /opt/galaxy/database/universe.sqlite
```

---

## All Flags Reference

| Flag | Short | Description |
| ---- | ----- | ----------- |
| `--method` | `-m` | `api` or `db` (default: `db`) |
| `--galaxy-url` | | Galaxy URL (default: `http://localhost:8080`) |
| `--api-key` | | Admin API key |
| `--db-path` | | Path to `universe.sqlite` |
| `--email` | `-e` | Filter by email (partial match) |
| `--username` | `-u` | Filter by username (partial match) |
| `--show-deleted` | | Include deleted users |
| `--all-fields` | | Show all DB fields (DB method only) |
| `--roles` | | List user-role assignments |
| `--user-id` | | User ID for role lookup |
| `--auth-type` | | List users by auth: `oauth`, `local`, or `all` (DB only) |
