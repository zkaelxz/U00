"""
tests/test_action_tiers.py -- the shared 🟢/🟡/🔴 action-permission-tier
classification (Step 57). No maintenance-assistant feature exists yet to
call this for real, so these tests exercise the classification directly
against the two behaviors it exists to guarantee: a 🔴-tier action can't
be waved through by a 🟡-tier confirmation, and touching the
classification's own code is always 🔴 no matter what the edit looks like.
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import action_tiers as at


def test_read_only_action_is_green():
    assert at.classify_action("run_tests") is at.ActionTier.GREEN


def test_code_edit_is_yellow():
    assert at.classify_action("modify_code", touches_paths=["translate_engines.py"]) is at.ActionTier.YELLOW


def test_delete_user_data_is_red():
    assert at.classify_action("delete_user_data") is at.ActionTier.RED


def test_unregistered_action_raises():
    with pytest.raises(ValueError):
        at.classify_action("do_something_undefined")


def test_green_action_needs_no_confirmation():
    tier = at.require_confirmation("run_tests", at.Confirmation.NONE)
    assert tier is at.ActionTier.GREEN


def test_yellow_action_satisfied_by_isolated_diff_confirmation():
    tier = at.require_confirmation(
        "modify_code", at.Confirmation.ISOLATED_DIFF_APPROVED, touches_paths=["dub.py"]
    )
    assert tier is at.ActionTier.YELLOW


def test_yellow_action_refused_without_any_confirmation():
    with pytest.raises(at.PermissionDenied):
        at.require_confirmation("modify_code", at.Confirmation.NONE, touches_paths=["dub.py"])


def test_red_action_refused_by_the_isolated_diff_confirmation_that_would_clear_yellow():
    # This is the exit condition's core guarantee: a 🔴 action attempted
    # with only the 🟡 flow's confirmation is refused, distinctly from a
    # real 🟡 action, which that same confirmation does clear.
    with pytest.raises(at.PermissionDenied):
        at.require_confirmation("delete_user_data", at.Confirmation.ISOLATED_DIFF_APPROVED)


def test_red_action_refused_with_no_confirmation_at_all():
    with pytest.raises(at.PermissionDenied):
        at.require_confirmation("overwrite_user_data", at.Confirmation.NONE)


def test_red_action_cleared_only_by_its_own_explicit_confirmation():
    tier = at.require_confirmation(
        "publish_to_shared_target", at.Confirmation.EXPLICIT_RED_CONFIRMED
    )
    assert tier is at.ActionTier.RED


def test_editing_this_modules_own_file_is_red_even_though_it_is_an_ordinary_code_edit():
    # The diff shape here is indistinguishable from any other "modify_code"
    # edit -- the RED classification comes only from the path touched.
    tier = at.classify_action("modify_code", touches_paths=["action_tiers.py"])
    assert tier is at.ActionTier.RED


def test_editing_a_future_maintenance_style_package_is_red():
    tier = at.classify_action("modify_code", touches_paths=["maintenance/tool_list.py"])
    assert tier is at.ActionTier.RED


def test_isolated_diff_confirmation_does_not_clear_a_protected_path_edit():
    with pytest.raises(at.PermissionDenied):
        at.require_confirmation(
            "modify_code",
            at.Confirmation.ISOLATED_DIFF_APPROVED,
            touches_paths=["permissions/tiers.py"],
        )


def test_unrelated_path_is_not_treated_as_protected():
    tier = at.classify_action("modify_code", touches_paths=["tabs/workspace_tab.py"])
    assert tier is at.ActionTier.YELLOW
