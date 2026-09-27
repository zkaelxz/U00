"""
action_tiers.py -- the shared 🟢/🟡/🔴 action-permission-tier classification
for anything an AI-driven feature in this app (starting with the future
in-app maintenance assistant, Step 42) might do on its own.

The problem this solves: Step 42's maintenance assistant and Step 43's
soft-delete/confirm-and-review feature each need to decide "can this run
automatically, does it need a diff shown first, or does it need a
separate, explicit yes" -- and without one shared place to check, every
new AI-driven feature would re-invent its own ad hoc version of that
judgment call. This module is that one place: a plain classification
other code imports and checks against, not a design document nobody
references.

Three tiers, in increasing order of caution:
  - GREEN  -- automatic, no confirmation needed: read-only actions.
  - YELLOW -- approval required, but isolated first: always happens on
              an isolated branch/worktree, shown as a diff, never
              applied directly.
  - RED    -- always requires its own explicit, separate confirmation --
              never satisfied by the same confirmation that clears a
              YELLOW action, even if the diff looks the same size.

One extra rule that isn't just "which bucket does this action fall
into": a change that touches this module, or any future
`maintenance`/`permissions`/`security`-style package, is always RED even
if its diff shape looks like an ordinary YELLOW code edit -- so an AI
session can never "fix" something by quietly loosening its own
restrictions without the user seeing that's what's happening.
"""
from __future__ import annotations

from enum import Enum


class ActionTier(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


TIER_EMOJI = {ActionTier.GREEN: "🟢", ActionTier.YELLOW: "🟡", ActionTier.RED: "🔴"}


class Confirmation(Enum):
    NONE = "none"
    ISOLATED_DIFF_APPROVED = "isolated_diff_approved"
    EXPLICIT_RED_CONFIRMED = "explicit_red_confirmed"


class PermissionDenied(Exception):
    """Raised when an action is attempted without the confirmation its tier requires."""


# 🟢 automatic -- read-only actions: run tests, run benchmarks, check
#    dependencies/model availability, inspect logs/Git history, generate
#    reports, clear caches.
# 🟡 approval required, but isolated first -- modify code/prompts/config,
#    add/change dependencies.
# 🔴 always explicit, separate confirmation -- delete or overwrite user
#    data, spend above a configured budget, change credentials/
#    authentication, publish or push to a shared/production target.
ACTION_TIERS: dict[str, ActionTier] = {
    "run_tests": ActionTier.GREEN,
    "run_benchmark": ActionTier.GREEN,
    "check_dependencies": ActionTier.GREEN,
    "check_model_availability": ActionTier.GREEN,
    "inspect_logs": ActionTier.GREEN,
    "inspect_git_history": ActionTier.GREEN,
    "generate_report": ActionTier.GREEN,
    "clear_cache": ActionTier.GREEN,
    "modify_code": ActionTier.YELLOW,
    "modify_prompt": ActionTier.YELLOW,
    "modify_config": ActionTier.YELLOW,
    "add_dependency": ActionTier.YELLOW,
    "change_dependency": ActionTier.YELLOW,
    "delete_user_data": ActionTier.RED,
    "overwrite_user_data": ActionTier.RED,
    "spend_above_budget": ActionTier.RED,
    "change_credentials": ActionTier.RED,
    "publish_to_shared_target": ActionTier.RED,
}

# Checked against every path an action touches (see `classify_action`). A
# hit forces RED regardless of what ACTION_TIERS says about the nominal
# action name -- this is the "the maintenance agent's own code is harder
# to modify than the rest of the app" guarantee.
_PROTECTED_MODULE_NAMES = frozenset({"action_tiers.py"})
_PROTECTED_PACKAGE_NAMES = frozenset({"maintenance", "permissions", "security"})

# Which Confirmation values satisfy which tier. RED's own confirmation
# also satisfies YELLOW (it's strictly more caution than YELLOW asks for),
# but YELLOW's isolated-diff confirmation never satisfies RED -- that
# asymmetry is the "never the same confirmation as a 🟡 action" rule.
TIER_REQUIREMENTS: dict[ActionTier, frozenset] = {
    ActionTier.GREEN: frozenset(Confirmation),
    ActionTier.YELLOW: frozenset(
        {Confirmation.ISOLATED_DIFF_APPROVED, Confirmation.EXPLICIT_RED_CONFIRMED}
    ),
    ActionTier.RED: frozenset({Confirmation.EXPLICIT_RED_CONFIRMED}),
}


def _touches_protected_module(paths) -> bool:
    for raw in paths:
        parts = [p for p in raw.replace("\\", "/").strip("/").split("/") if p]
        if not parts:
            continue
        if parts[-1] in _PROTECTED_MODULE_NAMES:
            return True
        if any(part in _PROTECTED_PACKAGE_NAMES for part in parts[:-1]):
            return True
    return False


def classify_action(action: str, *, touches_paths=()) -> ActionTier:
    """Returns the tier `action` falls under.

    `touches_paths` lists any file paths the action would change, if it's
    a code change -- pass this whenever available, since a protected-path
    hit overrides the action's nominal tier to RED.
    """
    if _touches_protected_module(touches_paths):
        return ActionTier.RED
    try:
        return ACTION_TIERS[action]
    except KeyError as exc:
        raise ValueError(
            f"{action!r} isn't in ACTION_TIERS -- register its tier before checking it"
        ) from exc


def require_confirmation(
    action: str, confirmation: Confirmation, *, touches_paths=()
) -> ActionTier:
    """Classifies `action` and raises PermissionDenied unless `confirmation`
    satisfies its tier. Returns the tier on success, for callers that want
    to log or display which tier just cleared."""
    tier = classify_action(action, touches_paths=touches_paths)
    allowed = TIER_REQUIREMENTS[tier]
    if confirmation not in allowed:
        raise PermissionDenied(
            f"{action!r} is tier {tier.value} ({TIER_EMOJI[tier]}) and needs one of "
            f"{sorted(c.value for c in allowed)}, got {confirmation.value!r}"
        )
    return tier
