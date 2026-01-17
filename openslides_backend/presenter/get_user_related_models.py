from typing import Any

import fastjsonschema

from openslides_backend.permissions.management_levels import CommitteeManagementLevel
from openslides_backend.shared.mixins.user_scope_mixin import UserScopeMixin
from openslides_backend.shared.schema import id_list_schema

from ..shared.schema import schema_version
from .base import BasePresenter
from .presenter import register_presenter

get_user_related_models_schema = fastjsonschema.compile(
    {
        "$schema": schema_version,
        "type": "object",
        "title": "get_user_related_models",
        "description": "get user ids related models",
        "properties": {
            "user_ids": id_list_schema,
        },
        "required": ["user_ids"],
        "additionalProperties": False,
    }
)


@register_presenter("get_user_related_models")
class GetUserRelatedModels(UserScopeMixin, BasePresenter):
    """
    Collects related models of the user_ids.
    """

    schema = get_user_related_models_schema

    def get_result(self) -> Any:
        result: dict[int, Any] = {}
        users = self.sql.get_many(
            "user",
            self.data["user_ids"],
            [
                "id",
                "organization_management_level",
                "meeting_user_ids",
                "committee_ids",
                "committee_management_ids",
            ],
        ) if self.data["user_ids"] else {}
        for user_id, user in users.items():
            result[user_id] = {}
            self.check_permissions_for_scope(user_id)
            if oml := user.get("organization_management_level"):
                result[user_id]["organization_management_level"] = oml
            if committees_data := self.get_committees_data(user):
                result[user_id]["committees"] = committees_data
            if meetings_data := self.get_meetings_data(user):
                result[user_id]["meetings"] = meetings_data
        return result

    def get_committees_data(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        if not user.get("committee_ids"):
            return []

        gm_result = self.sql.get_many(
            "committee", user["committee_ids"], ["id", "name"]
        )
        return [
            {
                "id": id_,
                "name": committee.get("name", ""),
                "cml": (
                    CommitteeManagementLevel.CAN_MANAGE
                    if id_ in user.get("committee_management_ids", [])
                    else ""
                ),
            }
            for id_, committee in gm_result.items()
        ]

    def get_meetings_data(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        if not (meeting_user_ids := user.get("meeting_user_ids")):
            return []

        result_fields = (
            "speaker_ids",
            "motion_submitter_ids",
            "assignment_candidate_ids",
            "locked_out",
        )
        meeting_users_data = self.sql.get_many(
            "meeting_user",
            meeting_user_ids,
            [*result_fields, "group_ids", "meeting_id"],
        ) if meeting_user_ids else {}
        meeting_users = [
            meeting_user
            for meeting_user in meeting_users_data.values()
            if meeting_user.pop("group_ids", None)
        ]

        if len(meeting_users) == 0:
            return []

        meeting_ids = [meeting_user["meeting_id"] for meeting_user in meeting_users]
        meetings = self.sql.get_many(
            "meeting",
            meeting_ids,
            ["id", "name", "is_active_in_organization_id", "locked_from_inside"],
        ) if meeting_ids else {}
        operator_user = self.sql.get(
            "user",
            self.user_id,
            ["meeting_ids"],
            lock_result=False,
        ) or {}
        operator_meetings = operator_user.get("meeting_ids", [])

        return [
            {
                "id": meeting["id"],
                "name": meeting.get("name"),
                "is_active_in_organization_id": meeting.get(
                    "is_active_in_organization_id"
                ),
                "is_locked": meeting.get("locked_from_inside", False),
                **{
                    field: value
                    for field in result_fields
                    if (value := meeting_user.get(field))
                    and (
                        not meeting.get("locked_from_inside")
                        or meeting["id"] in operator_meetings
                    )
                },
            }
            for meeting_user in meeting_users
            if (meeting := meetings.get(meeting_user["meeting_id"]))
        ]
