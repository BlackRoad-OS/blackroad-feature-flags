# blackroad-feature-flags

![CI](https://github.com/BlackRoad-OS/blackroad-feature-flags/actions/workflows/ci.yml/badge.svg)

Production-grade Python feature flag system — targeting rules, percentage rollout, audit trail, and a full CLI. Built on SQLite with zero external runtime dependencies.

## Features

- **14 targeting operators**: `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `contains`, `starts_with`, `ends_with`, `regex`, `semver_gte`, `semver_lt`
- **Percentage rollout** via consistent SHA-256 hashing (same user always gets the same result)
- **Audit trail** — every evaluation is recorded with reason and context hash
- **Bulk export/import** as JSON for backup and migration
- **Full CLI** and Python API
- **Zero runtime dependencies** — pure stdlib + SQLite

## Installation

```bash
git clone https://github.com/BlackRoad-OS/blackroad-feature-flags
cd blackroad-feature-flags
pip install -r requirements.txt   # dev only: pytest, flake8
```

## CLI Usage

```bash
# Create a flag (disabled, 0% rollout by default)
python -m src.feature_flags create dark-mode --desc "Dark UI" --tags beta,ui

# Enable / disable / toggle
python -m src.feature_flags enable dark-mode
python -m src.feature_flags disable dark-mode
python -m src.feature_flags toggle dark-mode

# Set rollout percentage (0-100)
python -m src.feature_flags rollout dark-mode 25

# Add targeting rules (first match wins)
python -m src.feature_flags rule dark-mode plan in "pro,enterprise"
python -m src.feature_flags rule dark-mode app_version semver_gte 3.0.0
python -m src.feature_flags rule dark-mode email ends_with "@blackroad.io"

# Evaluate a flag for a user+context
python -m src.feature_flags eval dark-mode --user user-123 \
  --context '{"plan":"pro","country":"US","app_version":"3.1.0"}'

# Evaluate ALL flags at once (SDK bootstrap)
python -m src.feature_flags all --user user-123 --context '{"plan":"free"}'

# List flags
python -m src.feature_flags list
python -m src.feature_flags list --enabled-only
python -m src.feature_flags list --tag beta

# Evaluation stats for last 24 h
python -m src.feature_flags stats dark-mode

# Export all flags as JSON
python -m src.feature_flags export > backup.json

# Delete a flag
python -m src.feature_flags delete dark-mode
```

## Python API

```python
from src.feature_flags import FeatureFlagSystem, Flag

ffs = FeatureFlagSystem(db_path="flags.db")

# Create
ffs.create_flag(Flag(
    name="new-dashboard",
    enabled=True,
    rollout_pct=20.0,
    description="Redesigned dashboard",
    tags=["ui", "beta"],
))

# Add targeting rule — always serve True to internal staff
ffs.add_targeting_rule("new_dashboard", "email", "ends_with", "@blackroad.io", serve=True)

# Evaluate
result = ffs.evaluate_flag("new_dashboard", {
    "user_id": "user-42",
    "email": "alice@blackroad.io",
    "plan": "enterprise",
})
print(result.value)   # True
print(result.reason)  # "targeting_rule"

# Evaluate all flags for a request context
flags = ffs.get_all_flags({"user_id": "user-42", "plan": "pro"})
# {"new_dashboard": True, "dark_mode": False, ...}

# Stats
stats = ffs.get_evaluation_stats("new_dashboard", hours=24)
# {"total_evaluations": 1200, "true_count": 240, "true_pct": 20.0, "reasons": {...}}

# Export / import
data = ffs.export_flags()
ffs.import_flags(data, overwrite=False)
```

## Targeting Rules

Rules are evaluated **in order**; the first match wins and returns its `serve` value. If no rule matches, rollout percentage applies.

| Operator | Description | Example value |
|----------|-------------|---------------|
| `eq` | Exact match | `"US"` |
| `neq` | Not equal | `"US"` |
| `in` | Value in comma-separated list | `"pro,enterprise"` |
| `not_in` | Value not in list | `"free,trial"` |
| `gt` | Numeric greater than | `"18"` |
| `gte` | Numeric greater than or equal | `"18"` |
| `lt` | Numeric less than | `"100"` |
| `lte` | Numeric less than or equal | `"100"` |
| `contains` | String contains (case-insensitive) | `"@blackroad"` |
| `starts_with` | String starts with (case-insensitive) | `"admin_"` |
| `ends_with` | String ends with (case-insensitive) | `"@blackroad.io"` |
| `regex` | Python regex search | `"^(pro\|ent)$"` |
| `semver_gte` | Semantic version >= | `"2.0.0"` |
| `semver_lt` | Semantic version < | `"3.0.0"` |

## Rollout Hashing

Rollout uses **consistent SHA-256 hashing** keyed on `"{flag_name}:{user_id}"`:

```
bucket = int(sha256(f"{flag_name}:{user_id}").hexdigest(), 16) % 10000 / 100
value  = bucket < rollout_pct
```

The same user always lands in the same bucket for a given flag — results are stable without sticky sessions or external storage.

## SQLite Schema

```sql
CREATE TABLE flags (
    name              TEXT PRIMARY KEY,
    enabled           INTEGER NOT NULL DEFAULT 0,
    rollout_pct       REAL    NOT NULL DEFAULT 0.0,
    rules_json        TEXT    NOT NULL DEFAULT '[]',
    description       TEXT    DEFAULT '',
    tags_json         TEXT    DEFAULT '[]',
    environments_json TEXT    DEFAULT '["production","staging","development"]',
    default_value     INTEGER DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    created_by        TEXT    DEFAULT 'system'
);

CREATE TABLE evaluations (
    id            TEXT PRIMARY KEY,
    flag_name     TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    result        INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    rule_matched  TEXT DEFAULT '',
    context_hash  TEXT NOT NULL,
    evaluated_at  TEXT NOT NULL
);
```

## Use Cases

| Use case | Approach |
|----------|----------|
| **Beta rollout** | `rollout_pct=10`, bump weekly |
| **Internal preview** | Rule: `email ends_with @company.io`, `serve=true` |
| **Instant kill switch** | `enabled=false` disables for all users immediately |
| **Plan gating** | Rule: `plan in "pro,enterprise"`, `serve=true` |
| **Version gating** | Rule: `app_version semver_gte 4.0.0` |
| **Regional rollout** | Rules per country, rollout fallback for the rest |
| **A/B test** | Two flags at 50% each; consistent hash prevents overlap |

## Running Tests

```bash
pytest tests/ -v --tb=short
```

## License

Proprietary — copyright BlackRoad OS, Inc. All rights reserved.
