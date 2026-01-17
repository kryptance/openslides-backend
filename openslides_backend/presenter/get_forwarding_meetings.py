from datetime import datetime
from typing import Any

import fastjsonschema

from ..permissions.permission_helper import has_perm
from ..permissions.permissions import Permissions
from ..shared.exceptions import PermissionDenied, PresenterException
from ..shared.schema import required_id_schema, schema_version
from .base import BasePresenter
from .presenter import register_presenter

get_forwarding_meetings_schema = fastjsonschema.compile(
    {
        "$schema": schema_version,
        "type": "object",
        "title": "get_forwarding_meetings",
        "description": "get forwarding meetings",
        "properties": {
            "meeting_id": required_id_schema,
        },
        "required": ["meeting_id"],
    }
)


@register_presenter("get_forwarding_meetings")
class GetForwardingMeetings(BasePresenter):
    """
    Get forwarded meetings.
    """

    schema = get_forwarding_meetings_schema

    def get_result(self) -> Any:
        # check permission
        if not has_perm(
            self.sql,
            self.user_id,
            Permissions.Motion.CAN_FORWARD,
            self.data["meeting_id"],
        ):
            msg = "You are not allowed to perform presenter get_forwarding_meetings"
            msg += f" Missing permission: {Permissions.Motion.CAN_FORWARD}"
            raise PermissionDenied(msg)

        meeting = self.sql.get(
            "meeting",
            self.data["meeting_id"],
            ["committee_id", "is_active_in_organization_id", "name"],
        ) or {}
        if not meeting.get("is_active_in_organization_id"):
            raise PresenterException(
                "Your sender meeting is an archived meeting, which can not forward motions."
            )

        committee = self.sql.get(
            "committee",
            meeting["committee_id"],
            ["forward_to_committee_ids"],
        ) or {}

        result = []
        for forward_to_committee_id in committee.get("forward_to_committee_ids", []):
            forward_to_committee = self.sql.get(
                "committee",
                forward_to_committee_id,
                ["meeting_ids", "name", "default_meeting_id"],
            ) or {}

            meeting_result = []
            for meeting_id2 in forward_to_committee.get("meeting_ids", []):
                meeting2 = self.sql.get(
                    "meeting",
                    meeting_id2,
                    ["name", "is_active_in_organization_id", "start_time", "end_time"],
                ) or {}
                if meeting2.get("is_active_in_organization_id"):
                    meeting_result.append(
                        {
                            "id": meeting_id2,
                            "name": meeting2.get("name", ""),
                            "start_time": self._get_formatted_datetime_value(
                                meeting2.get("start_time")
                            ),
                            "end_time": self._get_formatted_datetime_value(
                                meeting2.get("end_time")
                            ),
                        }
                    )
            if meeting_result:
                result.append(
                    {
                        "id": forward_to_committee_id,
                        "name": forward_to_committee.get("name", ""),
                        "meetings": meeting_result,
                        "default_meeting_id": forward_to_committee.get(
                            "default_meeting_id"
                        ),
                    }
                )
        return result

    @staticmethod
    def _get_formatted_datetime_value(value: Any) -> str | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
