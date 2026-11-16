"""
tests/test_action_tiers.py -- the shared 🟢/🟡/🔴 action-permission-tier
classification (Step 57). No maintenance-assistant feature exists yet to
call this for real, so these tests exercise the classification directly
against the behavior it exists to guarantee: touching the
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


def test_editing_this_modules_own_file_is_red_even_though_it_is_an_ordinary_code_edit():
    # The diff shape here is indistinguishable from any other "modify_code"
    # edit -- the RED classification comes only from the path touched.
    tier = at.classify_action("modify_code", touches_paths=["action_tiers.py"])
    assert tier is at.ActionTier.RED


def test_editing_a_future_maintenance_style_package_is_red():
    tier = at.classify_action("modify_code", touches_paths=["maintenance/tool_list.py"])
    assert tier is at.ActionTier.RED


def test_unrelated_path_is_not_treated_as_protected():
    tier = at.classify_action("modify_code", touches_paths=["tabs/workspace_tab.py"])
    assert tier is at.ActionTier.YELLOW
