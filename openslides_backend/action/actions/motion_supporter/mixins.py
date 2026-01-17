from typing import Any

from ....permissions.permission_helper import has_perm
from ....permissions.permissions import Permissions
from ....shared.exceptions import ActionException, BadCodingException, MissingPermission
from ...mixins.delegation_based_restriction_mixin import DelegationBasedRestrictionMixin
from ...util.typing import ActionData


class SupporterActionMixin(DelegationBasedRestrictionMixin):
    permission = Permissions.Motion.CAN_MANAGE_METADATA

    def check_permissions(self, instance: dict[str, Any]) -> None:
        if self.is_self_instance(instance):
            if not len(
                self.check_perm_and_delegator_restriction(
                    Permissions.Motion.CAN_MANAGE_METADATA,
                    "users_forbid_delegator_as_supporter",
                    [self.get_meeting_id(instance)],
                )
            ):
                meeting_id = self.get_meeting_id(instance)
                if not has_perm(
                    self.sql,
                    self.user_id,
                    Permissions.Motion.CAN_SUPPORT,
                    meeting_id,
                ):
                    raise MissingPermission(Permissions.Motion.CAN_SUPPORT)
        else:
            super().check_permissions(instance)

    def get_motion_id(self, instance: dict[str, Any]) -> int:
        raise BadCodingException("get_motion_id not implemented.")

    def get_meeting_user_id(self, instance: dict[str, Any]) -> int | None:
        raise BadCodingException("get_meeting_user_id not implemented.")

    def is_self_instance(self, instance: dict[str, Any]) -> bool:
        meeting_user_id = self.get_meeting_user_id(instance)
        if meeting_user_id:
            meeting_user = self.sql.get(
                "meeting_user", meeting_user_id, ["user_id"]
            ) or {}
            return meeting_user.get("user_id") == self.user_id
        return False

    def check_action_data(self, action_data: ActionData) -> ActionData:
        motion_ids = [self.get_motion_id(instance) for instance in action_data]
        motions = self.sql.get_many(
            "motion", motion_ids, ["meeting_id", "state_id", "supporter_ids"]
        ) if motion_ids else {}
        meeting_ids = list({mot["meeting_id"] for mot in motions.values()})
        meetings = self.sql.get_many(
            "meeting", meeting_ids, ["motions_supporters_min_amount"]
        ) if meeting_ids else {}
        state_ids = list({mot["state_id"] for mot in motions.values()})
        states = self.sql.get_many(
            "motion_state", state_ids, ["allow_support"]
        ) if state_ids else {}
        for instance in action_data:
            motion = motions.get(self.get_motion_id(instance), {})
            meeting_id = motion["meeting_id"]
            meeting = meetings.get(meeting_id, {})
            if meeting.get("motions_supporters_min_amount") == 0:
                raise ActionException("Motion supporters system deactivated.")
            if not has_perm(
                self.sql,
                self.user_id,
                Permissions.Motion.CAN_MANAGE_METADATA,
                meeting_id,
            ):
                state = states.get(motion["state_id"], {})

                if state.get("allow_support") is False:
                    raise ActionException("The state does not allow support.")
        return action_data
