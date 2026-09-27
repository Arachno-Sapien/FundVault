from django.test import SimpleTestCase

from apps.accounts.models import User
from apps.accounts.permissions import Action, can, needs_approval

OWNER, ADMIN, MEMBER, VIEWER = "owner", "admin", "member", "viewer"


def _user(role):
    return User(id="u", username="u", email="u@example.com", password_hash="x", role=role)


class CapabilityTests(SimpleTestCase):
    def test_unknown_role_can_do_nothing(self):
        self.assertFalse(can(_user("wizard"), Action.VIEW))

    def test_inactive_user_can_do_nothing(self):
        user = _user(OWNER)
        user.is_active = False
        self.assertFalse(can(user, Action.VIEW))

    def test_full_matrix(self):
        """Exhaustive (role, action) truth table from the spec's permission table.

        Every capability in the spec resolves to an Action constant except
        "Export / print reports" (covered by VIEW - no separate gate in the
        spec) and the approval-threshold row (covered by needs_approval,
        tested separately below).
        """
        matrix = {
            Action.VIEW: {OWNER: True, ADMIN: True, MEMBER: True, VIEWER: True},
            Action.CREATE_TXN: {OWNER: True, ADMIN: True, MEMBER: True, VIEWER: False},
            Action.APPROVE: {OWNER: True, ADMIN: True, MEMBER: False, VIEWER: False},
            Action.MODIFY_TXN: {OWNER: True, ADMIN: True, MEMBER: False, VIEWER: False},
            Action.MANAGE_FUNDS: {OWNER: True, ADMIN: True, MEMBER: False, VIEWER: False},
            Action.MANAGE_MEMBERS: {OWNER: True, ADMIN: True, MEMBER: False, VIEWER: False},
            Action.MINT_ADMIN_CODE: {OWNER: True, ADMIN: False, MEMBER: False, VIEWER: False},
            Action.CHANGE_ROLE: {OWNER: True, ADMIN: False, MEMBER: False, VIEWER: False},
            Action.MANAGE_ORG_CONFIG: {OWNER: True, ADMIN: False, MEMBER: False, VIEWER: False},
            Action.TRANSFER_OWNERSHIP: {OWNER: True, ADMIN: False, MEMBER: False, VIEWER: False},
        }
        for action, expected_by_role in matrix.items():
            for role, expected in expected_by_role.items():
                actual = can(_user(role), action)
                self.assertEqual(
                    actual,
                    expected,
                    f"can(role={role}, action={action}) was {actual}, expected {expected}",
                )


class ApprovalRuleTests(SimpleTestCase):
    def test_member_over_threshold_needs_approval(self):
        self.assertTrue(needs_approval(_user(MEMBER), 5000, 1000))

    def test_member_under_threshold_does_not(self):
        self.assertFalse(needs_approval(_user(MEMBER), 500, 1000))

    def test_member_exactly_at_threshold_needs_approval(self):
        self.assertTrue(needs_approval(_user(MEMBER), 1000, 1000))

    def test_admin_never_needs_approval(self):
        self.assertFalse(needs_approval(_user(ADMIN), 999999, 1000))

    def test_owner_never_needs_approval(self):
        self.assertFalse(needs_approval(_user(OWNER), 999999, 1000))

    def test_viewer_never_needs_approval(self):
        # Viewers can't create transactions at all; needs_approval must still
        # be well-defined (False) rather than raising, since callers may check
        # it uniformly before the create-permission check.
        self.assertFalse(needs_approval(_user(VIEWER), 999999, 1000))

    def test_zero_threshold_disables_approval_entirely(self):
        self.assertFalse(needs_approval(_user(MEMBER), 999999, 0))
