import pytest
import json
from src.feature_flags import FeatureFlagSystem, Flag, TargetingRule, EvaluationResult


def make_ffs(tmp_path):
    return FeatureFlagSystem(db_path=str(tmp_path / "flags.db"))


def test_create_and_get_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    flag = Flag(name="new-feature", enabled=True, rollout_pct=100.0, description="Test flag")
    ffs.create_flag(flag)
    fetched = ffs.get_flag("new_feature")
    assert fetched is not None
    assert fetched.enabled is True
    assert fetched.rollout_pct == 100.0


def test_duplicate_flag_raises(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="dup", enabled=False, rollout_pct=0.0))
    with pytest.raises(ValueError, match="already exists"):
        ffs.create_flag(Flag(name="dup", enabled=False, rollout_pct=0.0))


def test_evaluate_disabled_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="off-flag", enabled=False, rollout_pct=100.0))
    result = ffs.evaluate_flag("off_flag", {"user_id": "u1"})
    assert result.value is False
    assert result.reason == "flag_disabled"


def test_evaluate_full_rollout(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="full-rollout", enabled=True, rollout_pct=100.0))
    result = ffs.evaluate_flag("full_rollout", {"user_id": "u1"})
    assert result.value is True
    assert "rollout_100" in result.reason


def test_evaluate_zero_rollout(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="zero-rollout", enabled=True, rollout_pct=0.0))
    result = ffs.evaluate_flag("zero_rollout", {"user_id": "u1"})
    assert result.value is False


def test_evaluate_missing_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    result = ffs.evaluate_flag("nonexistent", {"user_id": "u1"})
    assert result.value is False
    assert result.reason == "flag_not_found"


def test_targeting_rule_eq(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="targeted", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("targeted", "country", "eq", "US", serve=True)
    result_us = ffs.evaluate_flag("targeted", {"user_id": "u1", "country": "US"})
    result_uk = ffs.evaluate_flag("targeted", {"user_id": "u2", "country": "UK"})
    assert result_us.value is True
    assert result_us.reason == "targeting_rule"
    assert result_uk.value is False


def test_targeting_rule_in(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="in-test", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("in_test", "plan", "in", "pro,enterprise", serve=True)
    assert ffs.evaluate_flag("in_test", {"user_id": "u1", "plan": "pro"}).value is True
    assert ffs.evaluate_flag("in_test", {"user_id": "u2", "plan": "free"}).value is False


def test_targeting_rule_gt(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="gt-test", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("gt_test", "age", "gt", "18", serve=True)
    assert ffs.evaluate_flag("gt_test", {"user_id": "u1", "age": "25"}).value is True
    assert ffs.evaluate_flag("gt_test", {"user_id": "u2", "age": "15"}).value is False


def test_targeting_rule_contains(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="contains-test", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("contains_test", "email", "contains", "@blackroad", serve=True)
    assert ffs.evaluate_flag("contains_test", {"user_id": "u1", "email": "alice@blackroad.io"}).value is True
    assert ffs.evaluate_flag("contains_test", {"user_id": "u2", "email": "bob@gmail.com"}).value is False


def test_targeting_rule_semver(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="semver-test", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("semver_test", "app_version", "semver_gte", "2.0.0", serve=True)
    assert ffs.evaluate_flag("semver_test", {"user_id": "u1", "app_version": "2.1.0"}).value is True
    assert ffs.evaluate_flag("semver_test", {"user_id": "u2", "app_version": "1.9.9"}).value is False


def test_toggle_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="togglable", enabled=False, rollout_pct=0.0))
    ffs.toggle_flag("togglable")
    assert ffs.get_flag("togglable").enabled is True
    ffs.toggle_flag("togglable")
    assert ffs.get_flag("togglable").enabled is False


def test_enable_disable_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="ed-flag", enabled=False, rollout_pct=0.0))
    ffs.enable_flag("ed_flag")
    assert ffs.get_flag("ed_flag").enabled is True
    ffs.disable_flag("ed_flag")
    assert ffs.get_flag("ed_flag").enabled is False


def test_set_rollout(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="roll-flag", enabled=True, rollout_pct=0.0))
    ffs.set_rollout("roll_flag", 75.0)
    assert ffs.get_flag("roll_flag").rollout_pct == 75.0


def test_invalid_rollout_raises(tmp_path):
    ffs = make_ffs(tmp_path)
    with pytest.raises(ValueError):
        Flag(name="bad", enabled=True, rollout_pct=101.0)


def test_get_all_flags(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="flag-a", enabled=True, rollout_pct=100.0))
    ffs.create_flag(Flag(name="flag-b", enabled=False, rollout_pct=0.0))
    all_flags = ffs.get_all_flags({"user_id": "u1"})
    assert "flag_a" in all_flags
    assert all_flags["flag_a"] is True
    assert all_flags["flag_b"] is False


def test_rollout_consistency(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="pct-flag", enabled=True, rollout_pct=50.0))
    user_id = "consistent-user"
    results = [ffs.evaluate_flag("pct_flag", {"user_id": user_id}, record=False).value for _ in range(10)]
    assert all(r == results[0] for r in results)  # always same result for same user


def test_rollout_distribution(tmp_path):
    """~50% rollout should serve ~50% of unique users."""
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="dist-flag", enabled=True, rollout_pct=50.0))
    results = [
        ffs.evaluate_flag("dist_flag", {"user_id": f"user-{i}"}, record=False).value
        for i in range(1000)
    ]
    true_count = sum(results)
    assert 400 <= true_count <= 600, f"Expected ~500/1000, got {true_count}"


def test_list_flags(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="list-a", enabled=True, rollout_pct=0.0))
    ffs.create_flag(Flag(name="list-b", enabled=False, rollout_pct=0.0))
    all_flags = ffs.list_flags()
    assert len(all_flags) == 2
    enabled = ffs.list_flags(enabled_only=True)
    assert len(enabled) == 1


def test_list_flags_by_tag(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="tagged", enabled=True, rollout_pct=0.0, tags=["beta"]))
    ffs.create_flag(Flag(name="untagged", enabled=True, rollout_pct=0.0))
    beta_flags = ffs.list_flags(tag="beta")
    assert len(beta_flags) == 1
    assert beta_flags[0].name == "tagged"


def test_remove_targeting_rule(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="rule-rm", enabled=True, rollout_pct=0.0))
    ffs.add_targeting_rule("rule_rm", "country", "eq", "US")
    assert len(ffs.get_flag("rule_rm").rules) == 1
    ffs.remove_targeting_rule("rule_rm", 0)
    assert len(ffs.get_flag("rule_rm").rules) == 0


def test_export_and_import(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="export-me", enabled=True, rollout_pct=60.0, description="Export test"))
    exported = ffs.export_flags()
    assert len(exported) == 1
    ffs2 = FeatureFlagSystem(db_path=str(tmp_path / "flags2.db"))
    result = ffs2.import_flags(exported)
    assert result["created"] == 1
    assert result["errors"] == []
    assert ffs2.get_flag("export_me") is not None


def test_import_skip_existing(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="existing", enabled=True, rollout_pct=0.0))
    exported = ffs.export_flags()
    result = ffs.import_flags(exported, overwrite=False)
    assert result["skipped"] == 1


def test_import_overwrite(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="overwrite-me", enabled=False, rollout_pct=0.0))
    new_data = [{"name": "overwrite_me", "enabled": True, "rollout_pct": 100.0}]
    result = ffs.import_flags(new_data, overwrite=True)
    assert result["updated"] == 1
    assert ffs.get_flag("overwrite_me").enabled is True


def test_delete_flag(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="delete-me", enabled=True, rollout_pct=0.0))
    assert ffs.delete_flag("delete_me") is True
    assert ffs.get_flag("delete_me") is None
    assert ffs.delete_flag("delete_me") is False


def test_evaluation_stats(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="stats-flag", enabled=True, rollout_pct=100.0))
    for i in range(5):
        ffs.evaluate_flag("stats_flag", {"user_id": f"u{i}"}, record=True)
    stats = ffs.get_evaluation_stats("stats_flag")
    assert stats["true_count"] == 5
    assert stats["true_pct"] == 100.0


def test_invalid_operator_raises(tmp_path):
    ffs = make_ffs(tmp_path)
    ffs.create_flag(Flag(name="op-test", enabled=True, rollout_pct=0.0))
    with pytest.raises(ValueError, match="Unknown operator"):
        ffs.add_targeting_rule("op_test", "attr", "INVALID_OP", "val")
