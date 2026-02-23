#!/usr/bin/env python3
"""BlackRoad Feature Flag System - Production-grade feature flag management with targeting and rollout."""
from __future__ import annotations
import argparse, hashlib, json, sqlite3, sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Rule operators
OPERATORS = {
    "eq": lambda a, b: str(a) == str(b),
    "neq": lambda a, b: str(a) != str(b),
    "in": lambda a, b: str(a) in [x.strip() for x in str(b).split(",")],
    "not_in": lambda a, b: str(a) not in [x.strip() for x in str(b).split(",")],
    "gt": lambda a, b: _safe_float(a) > _safe_float(b),
    "gte": lambda a, b: _safe_float(a) >= _safe_float(b),
    "lt": lambda a, b: _safe_float(a) < _safe_float(b),
    "lte": lambda a, b: _safe_float(a) <= _safe_float(b),
    "contains": lambda a, b: str(b).lower() in str(a).lower(),
    "starts_with": lambda a, b: str(a).lower().startswith(str(b).lower()),
    "ends_with": lambda a, b: str(a).lower().endswith(str(b).lower()),
    "regex": lambda a, b: bool(__import__("re").search(b, str(a))),
    "semver_gte": lambda a, b: _semver_gte(str(a), str(b)),
    "semver_lt": lambda a, b: not _semver_gte(str(a), str(b)),
}


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _semver_gte(a: str, b: str) -> bool:
    """Compare semver strings: returns True if a >= b."""
    def parse(s):
        parts = s.strip().lstrip("v").split(".")
        result = []
        for p in parts[:3]:
            try:
                result.append(int(p))
            except ValueError:
                result.append(0)
        while len(result) < 3:
            result.append(0)
        return result
    return parse(a) >= parse(b)


@dataclass
class TargetingRule:
    attribute: str
    operator: str
    value: str
    serve: bool = True  # what value to return when rule matches

    def evaluate(self, context: Dict[str, Any]) -> Optional[bool]:
        """Evaluate this rule against a context. Returns serve value or None if no match."""
        attr_val = context.get(self.attribute)
        if attr_val is None:
            return None
        op_fn = OPERATORS.get(self.operator)
        if op_fn is None:
            return None
        try:
            matches = op_fn(attr_val, self.value)
        except Exception:
            matches = False
        return self.serve if matches else None


@dataclass
class Flag:
    name: str
    enabled: bool
    rollout_pct: float
    rules: List[TargetingRule] = field(default_factory=list)
    description: str = ""
    tags: List[str] = field(default_factory=list)
    environments: List[str] = field(default_factory=lambda: ["production", "staging", "development"])
    default_value: bool = False
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    created_by: str = "system"

    def __post_init__(self):
        if not (0.0 <= self.rollout_pct <= 100.0):
            raise ValueError(f"rollout_pct must be 0-100, got {self.rollout_pct}")
        self.name = self.name.lower().replace(" ", "_").replace("-", "_")

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EvaluationResult:
    flag_name: str
    value: bool
    reason: str
    rule_matched: Optional[str]
    user_id: str
    context_hash: str


class FeatureFlagSystem:
    """Production feature flag system with targeting, rollout, and audit trail."""

    def __init__(self, db_path: str = "feature_flags.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS flags (
                    name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    rollout_pct REAL NOT NULL DEFAULT 0.0,
                    rules_json TEXT NOT NULL DEFAULT '[]',
                    description TEXT DEFAULT '',
                    tags_json TEXT DEFAULT '[]',
                    environments_json TEXT DEFAULT '["production","staging","development"]',
                    default_value INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT DEFAULT 'system'
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY,
                    flag_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    result INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    rule_matched TEXT DEFAULT '',
                    context_hash TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (flag_name) REFERENCES flags(name)
                );
                CREATE INDEX IF NOT EXISTS idx_eval_flag ON evaluations(flag_name);
                CREATE INDEX IF NOT EXISTS idx_eval_user ON evaluations(user_id);
                CREATE INDEX IF NOT EXISTS idx_eval_ts ON evaluations(evaluated_at);
                CREATE INDEX IF NOT EXISTS idx_flags_enabled ON flags(enabled);
            """)

    def _gen_id(self) -> str:
        ts = datetime.utcnow().isoformat()
        return "eval-" + hashlib.sha256(ts.encode()).hexdigest()[:12]

    def _context_hash(self, context: Dict) -> str:
        s = json.dumps(context, sort_keys=True)
        return hashlib.md5(s.encode()).hexdigest()[:8]

    def create_flag(self, flag: Flag) -> Flag:
        """Create a new feature flag."""
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute("SELECT name FROM flags WHERE name=?", (flag.name,)).fetchone()
            if existing:
                raise ValueError(f"Flag '{flag.name}' already exists.")
            rules_json = json.dumps([asdict(r) for r in flag.rules])
            conn.execute(
                "INSERT INTO flags VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (flag.name, int(flag.enabled), flag.rollout_pct, rules_json,
                 flag.description, json.dumps(flag.tags), json.dumps(flag.environments),
                 int(flag.default_value), flag.created_at, flag.updated_at, flag.created_by)
            )
        return flag

    def evaluate_flag(self, flag_name: str, context: Dict[str, Any],
                      record: bool = True) -> EvaluationResult:
        """Evaluate a feature flag for a given context."""
        flag = self.get_flag(flag_name)
        if not flag:
            return EvaluationResult(
                flag_name=flag_name, value=False, reason="flag_not_found",
                rule_matched=None, user_id=context.get("user_id", "anonymous"),
                context_hash=self._context_hash(context),
            )

        user_id = str(context.get("user_id", "anonymous"))
        ctx_hash = self._context_hash(context)

        # 1. Flag disabled globally
        if not flag.enabled:
            result = EvaluationResult(
                flag_name=flag_name, value=flag.default_value,
                reason="flag_disabled", rule_matched=None,
                user_id=user_id, context_hash=ctx_hash,
            )
            if record:
                self._record_eval(result)
            return result

        # 2. Check targeting rules (first match wins)
        for i, rule in enumerate(flag.rules):
            rule_result = rule.evaluate(context)
            if rule_result is not None:
                result = EvaluationResult(
                    flag_name=flag_name, value=rule_result,
                    reason="targeting_rule",
                    rule_matched=f"rule_{i}:{rule.attribute}:{rule.operator}:{rule.value}",
                    user_id=user_id, context_hash=ctx_hash,
                )
                if record:
                    self._record_eval(result)
                return result

        # 3. Rollout percentage via consistent hashing
        if flag.rollout_pct >= 100.0:
            value = True
            reason = "rollout_100"
        elif flag.rollout_pct <= 0.0:
            value = False
            reason = "rollout_0"
        else:
            hash_input = f"{flag_name}:{user_id}"
            hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
            bucket = (hash_val % 10000) / 100.0  # 0.00-99.99
            value = bucket < flag.rollout_pct
            reason = f"rollout_{flag.rollout_pct:.1f}pct_bucket_{bucket:.2f}"

        result = EvaluationResult(
            flag_name=flag_name, value=value, reason=reason,
            rule_matched=None, user_id=user_id, context_hash=ctx_hash,
        )
        if record:
            self._record_eval(result)
        return result

    def get_all_flags(self, context: Dict[str, Any], record: bool = False) -> Dict[str, bool]:
        """Evaluate all flags for a given context. Returns {flag_name: bool}."""
        return {
            flag.name: self.evaluate_flag(flag.name, context, record=record).value
            for flag in self.list_flags()
        }

    def toggle_flag(self, name: str) -> Flag:
        """Toggle a flag's enabled state."""
        flag = self.get_flag(name)
        if not flag:
            raise KeyError(f"Flag not found: {name}")
        new_state = not flag.enabled
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE flags SET enabled=?, updated_at=? WHERE name=?",
                         (int(new_state), now, name))
        flag.enabled = new_state
        flag.updated_at = now
        return flag

    def enable_flag(self, name: str) -> Flag:
        """Enable a flag."""
        flag = self.get_flag(name)
        if not flag:
            raise KeyError(f"Flag not found: {name}")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE flags SET enabled=1, updated_at=? WHERE name=?", (now, name))
        flag.enabled = True
        return flag

    def disable_flag(self, name: str) -> Flag:
        """Disable a flag."""
        flag = self.get_flag(name)
        if not flag:
            raise KeyError(f"Flag not found: {name}")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE flags SET enabled=0, updated_at=? WHERE name=?", (now, name))
        flag.enabled = False
        return flag

    def set_rollout(self, name: str, pct: float) -> Flag:
        """Set rollout percentage for a flag (0-100)."""
        if not (0.0 <= pct <= 100.0):
            raise ValueError(f"pct must be 0-100, got {pct}")
        flag = self.get_flag(name)
        if not flag:
            raise KeyError(f"Flag not found: {name}")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE flags SET rollout_pct=?, updated_at=? WHERE name=?", (pct, now, name))
        flag.rollout_pct = pct
        return flag

    def add_targeting_rule(self, flag_name: str, attribute: str, operator: str,
                           value: str, serve: bool = True) -> Flag:
        """Add a targeting rule to a flag."""
        if operator not in OPERATORS:
            raise ValueError(f"Unknown operator: {operator}. Valid: {list(OPERATORS.keys())}")
        flag = self.get_flag(flag_name)
        if not flag:
            raise KeyError(f"Flag not found: {flag_name}")
        rule = TargetingRule(attribute=attribute, operator=operator, value=value, serve=serve)
        flag.rules.append(rule)
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE flags SET rules_json=?, updated_at=? WHERE name=?",
                (json.dumps([asdict(r) for r in flag.rules]), now, flag_name)
            )
        return flag

    def remove_targeting_rule(self, flag_name: str, index: int) -> Flag:
        """Remove a targeting rule by index."""
        flag = self.get_flag(flag_name)
        if not flag:
            raise KeyError(f"Flag not found: {flag_name}")
        if index < 0 or index >= len(flag.rules):
            raise IndexError(f"Rule index {index} out of range (flag has {len(flag.rules)} rules)")
        flag.rules.pop(index)
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE flags SET rules_json=?, updated_at=? WHERE name=?",
                (json.dumps([asdict(r) for r in flag.rules]), now, flag_name)
            )
        return flag

    def get_flag(self, name: str) -> Optional[Flag]:
        """Fetch a flag by name."""
        normalized = name.lower().replace("-", "_")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM flags WHERE name=?", (normalized,)).fetchone()
            if not row:
                return None
            rules = [TargetingRule(**r) for r in json.loads(row["rules_json"])]
            return Flag(
                name=row["name"], enabled=bool(row["enabled"]),
                rollout_pct=row["rollout_pct"], rules=rules,
                description=row["description"],
                tags=json.loads(row["tags_json"]),
                environments=json.loads(row["environments_json"]),
                default_value=bool(row["default_value"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
                created_by=row["created_by"],
            )

    def list_flags(self, enabled_only: bool = False, tag: Optional[str] = None) -> List[Flag]:
        """List all flags with optional filters."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM flags WHERE 1=1"
            params: List = []
            if enabled_only:
                query += " AND enabled=1"
            query += " ORDER BY name"
            rows = conn.execute(query, params).fetchall()

        flags = []
        for row in rows:
            rules = [TargetingRule(**r) for r in json.loads(row["rules_json"])]
            flag = Flag(
                name=row["name"], enabled=bool(row["enabled"]),
                rollout_pct=row["rollout_pct"], rules=rules,
                description=row["description"],
                tags=json.loads(row["tags_json"]),
                environments=json.loads(row["environments_json"]),
                default_value=bool(row["default_value"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
                created_by=row["created_by"],
            )
            if tag and tag not in flag.tags:
                continue
            flags.append(flag)
        return flags

    def get_evaluation_stats(self, flag_name: str, hours: int = 24) -> Dict:
        """Get evaluation statistics for a flag over the last N hours."""
        cutoff_str = datetime.utcnow().isoformat()[:13]  # YYYY-MM-DDTHH
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM evaluations WHERE flag_name=? AND evaluated_at >= ?",
                (flag_name, cutoff_str)
            ).fetchone()[0]
            true_count = conn.execute(
                "SELECT COUNT(*) FROM evaluations WHERE flag_name=? AND result=1 AND evaluated_at >= ?",
                (flag_name, cutoff_str)
            ).fetchone()[0]
            reason_rows = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM evaluations WHERE flag_name=? GROUP BY reason",
                (flag_name,)
            ).fetchall()
        return {
            "flag_name": flag_name,
            "total_evaluations": total,
            "true_count": true_count,
            "false_count": total - true_count,
            "true_pct": round(true_count / total * 100, 1) if total > 0 else 0.0,
            "reasons": {r[0]: r[1] for r in reason_rows},
        }

    def delete_flag(self, name: str) -> bool:
        """Delete a flag and all its evaluations."""
        normalized = name.lower().replace("-", "_")
        with sqlite3.connect(self.db_path) as conn:
            deleted = conn.execute("DELETE FROM flags WHERE name=?", (normalized,)).rowcount
            conn.execute("DELETE FROM evaluations WHERE flag_name=?", (normalized,))
        return deleted > 0

    def export_flags(self) -> List[Dict]:
        """Export all flags as a list of dicts (for backup/import)."""
        return [f.to_dict() for f in self.list_flags()]

    def import_flags(self, flags_data: List[Dict], overwrite: bool = False) -> Dict:
        """Import flags from a list of dicts."""
        created = updated = skipped = 0
        errors: List[str] = []
        for fd in flags_data:
            try:
                rules = [TargetingRule(**r) for r in fd.get("rules", [])]
                flag = Flag(
                    name=fd["name"], enabled=fd.get("enabled", False),
                    rollout_pct=fd.get("rollout_pct", 0.0), rules=rules,
                    description=fd.get("description", ""),
                    tags=fd.get("tags", []),
                    environments=fd.get("environments", ["production"]),
                    default_value=fd.get("default_value", False),
                )
                existing = self.get_flag(flag.name)
                if existing:
                    if overwrite:
                        self.delete_flag(flag.name)
                        self.create_flag(flag)
                        updated += 1
                    else:
                        skipped += 1
                else:
                    self.create_flag(flag)
                    created += 1
            except Exception as e:
                errors.append(f"{fd.get('name', 'unknown')}: {e}")
        return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}

    def _record_eval(self, result: EvaluationResult):
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?)",
                    (self._gen_id(), result.flag_name, result.user_id,
                     int(result.value), result.reason,
                     result.rule_matched or "",
                     result.context_hash, datetime.utcnow().isoformat())
                )
            except sqlite3.Error:
                pass  # Non-critical: don't fail evaluations on audit errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feature-flags", description="BlackRoad Feature Flag System")
    parser.add_argument("--db", default="feature_flags.db")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create", help="Create a new flag")
    create.add_argument("name")
    create.add_argument("--desc", default="")
    create.add_argument("--rollout", type=float, default=0.0)
    create.add_argument("--enabled", action="store_true")
    create.add_argument("--tags", default="")

    toggle = sub.add_parser("toggle", help="Toggle flag enabled state")
    toggle.add_argument("name")

    enable = sub.add_parser("enable", help="Enable a flag")
    enable.add_argument("name")

    disable = sub.add_parser("disable", help="Disable a flag")
    disable.add_argument("name")

    rollout = sub.add_parser("rollout", help="Set rollout percentage")
    rollout.add_argument("name")
    rollout.add_argument("pct", type=float)

    rule = sub.add_parser("rule", help="Add a targeting rule")
    rule.add_argument("flag")
    rule.add_argument("attribute")
    rule.add_argument("operator")
    rule.add_argument("value")
    rule.add_argument("--serve", type=lambda x: x.lower() == "true", default=True)

    eval_cmd = sub.add_parser("eval", help="Evaluate flag for context")
    eval_cmd.add_argument("flag")
    eval_cmd.add_argument("--user", default="anonymous")
    eval_cmd.add_argument("--context", default="{}", help="JSON context string")

    all_cmd = sub.add_parser("all", help="Evaluate all flags for context")
    all_cmd.add_argument("--user", default="anonymous")
    all_cmd.add_argument("--context", default="{}", help="JSON context string")

    lst = sub.add_parser("list", help="List all flags")
    lst.add_argument("--enabled-only", action="store_true")
    lst.add_argument("--tag", default="")

    stats = sub.add_parser("stats", help="Evaluation stats for a flag")
    stats.add_argument("name")

    sub.add_parser("export", help="Export all flags as JSON")

    del_cmd = sub.add_parser("delete", help="Delete a flag")
    del_cmd.add_argument("name")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    ffs = FeatureFlagSystem(db_path=args.db)

    if args.command == "create":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        flag = Flag(name=args.name, enabled=args.enabled, rollout_pct=args.rollout,
                    description=args.desc, tags=tags)
        ffs.create_flag(flag)
        state = "enabled" if flag.enabled else "disabled"
        print(f"Created flag '{flag.name}' ({state}, rollout={flag.rollout_pct}%)")
    elif args.command == "toggle":
        flag = ffs.toggle_flag(args.name)
        print(f"Flag '{flag.name}' is now {'enabled' if flag.enabled else 'disabled'}")
    elif args.command == "enable":
        ffs.enable_flag(args.name)
        print(f"Flag '{args.name}' enabled")
    elif args.command == "disable":
        ffs.disable_flag(args.name)
        print(f"Flag '{args.name}' disabled")
    elif args.command == "rollout":
        flag = ffs.set_rollout(args.name, args.pct)
        print(f"Flag '{args.name}' rollout set to {flag.rollout_pct}%")
    elif args.command == "rule":
        ffs.add_targeting_rule(args.flag, args.attribute, args.operator, args.value, serve=args.serve)
        print(f"Added rule to '{args.flag}': {args.attribute} {args.operator} {args.value} => {args.serve}")
    elif args.command == "eval":
        ctx = json.loads(args.context)
        ctx["user_id"] = args.user
        result = ffs.evaluate_flag(args.flag, ctx)
        print(json.dumps({"flag": result.flag_name, "value": result.value,
                          "reason": result.reason, "user": result.user_id}, indent=2))
    elif args.command == "all":
        ctx = json.loads(args.context)
        ctx["user_id"] = args.user
        flags = ffs.get_all_flags(ctx)
        print(json.dumps(flags, indent=2))
    elif args.command == "list":
        flags = ffs.list_flags(enabled_only=args.enabled_only, tag=args.tag or None)
        if not flags:
            print("No flags found.")
        for f in flags:
            state = "ON " if f.enabled else "OFF"
            tags_str = f"  tags=[{','.join(f.tags)}]" if f.tags else ""
            print(f"  [{state}] {f.name:<35} rollout={f.rollout_pct:>6.1f}%  rules={len(f.rules)}{tags_str}")
    elif args.command == "stats":
        stats_data = ffs.get_evaluation_stats(args.name)
        print(json.dumps(stats_data, indent=2))
    elif args.command == "export":
        data = ffs.export_flags()
        print(json.dumps(data, indent=2))
    elif args.command == "delete":
        ok = ffs.delete_flag(args.name)
        print(f"Deleted '{args.name}'" if ok else f"Flag '{args.name}' not found")


if __name__ == "__main__":
    main()
