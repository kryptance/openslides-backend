from typing import Any, cast

from ....permissions.management_levels import (
    CommitteeManagementLevel,
    OrganizationManagementLevel,
)
from ....permissions.permission_helper import has_committee_management_level, has_perm
from ....permissions.permissions import Permissions
from ....shared.exceptions import ActionException, MissingPermission
from ...action import Action
from ...mixins.check_unique_name_mixin import CheckUniqueInContextMixin


class MeetingPermissionMixin(CheckUniqueInContextMixin):
    def validate_instance(self, instance: dict[str, Any]) -> None:
        super().validate_instance(instance)
        if instance.get("external_id"):
            self.check_unique_in_context(
                "external_id",
                instance["external_id"],
                "The external id of the meeting is not unique in the organization scope. Send a differing external id with this request.",
                None,
            )

    def check_permissions(self, instance: dict[str, Any]) -> None:
        committee_id = self.get_committee_id(instance)
        if id_ := (instance.get("id") or instance.get("meeting_id")):
            meeting = self.sql.get("meeting", id_, ["locked_from_inside"]) or {}
            if meeting.get("locked_from_inside"):
                user = self.sql.get(
                    "user", self.user_id, ["organization_management_level"]
                ) or {}
                if (
                    user.get("organization_management_level")
                    != OrganizationManagementLevel.SUPERADMIN
                ) and not has_perm(
                    self.sql,
                    self.user_id,
                    Permissions.Meeting.CAN_MANAGE_SETTINGS,
                    id_,
                ):
                    if hasattr(self, "action_name"):
                        raise ActionException(f"Cannot {self.action_name} locked meeting.")
                    else:
                        raise ActionException(
                            "Cannot perform this action for the locked meeting."
                        )

        if not has_committee_management_level(
            self.sql,
            self.user_id,
            committee_id,
        ):
            raise MissingPermission({CommitteeManagementLevel.CAN_MANAGE: committee_id})

    def get_committee_id(self, instance: dict[str, Any]) -> int:
        return instance["committee_id"]


class MeetingCheckTimesMixin(Action):
    def check_start_and_end_time(
        self, instance: dict[str, Any], db_instance: dict[str, Any] | None = None
    ) -> None:
        if not ("start_time" in instance or "end_time" in instance):
            return
        if db_instance is None:
            db_instance = self.sql.get(
                "meeting", instance["id"], ["start_time", "end_time"]
            ) or {}
        start_time = instance.get("start_time", db_instance.get("start_time"))
        end_time = instance.get("end_time", db_instance.get("end_time"))
        if start_time and not end_time or not start_time and end_time:
            raise ActionException("Only one of start_time and end_time is not allowed.")
        if start_time and end_time and start_time > end_time:
            raise ActionException("start_time must be before end_time.")


class GetMeetingIdFromIdMixin(Action):
    def get_meeting_id(self, instance: dict[str, Any]) -> int:
        return cast(int, instance.get("id"))
