from typing import Any

import fastjsonschema

from ..permissions.permission_helper import has_perm
from ..permissions.permissions import Permissions
from ..shared.exceptions import PermissionDenied
from ..shared.schema import required_id_schema, schema_version
from .base import BasePresenter
from .presenter import register_presenter

get_forwarding_committees_schema = fastjsonschema.compile(
    {
        "$schema": schema_version,
        "type": "object",
        "title": "get_forwarding_committees",
        "description": "get forwarding committees",
        "properties": {
            "meeting_id": required_id_schema,
        },
        "required": ["meeting_id"],
    }
)


@register_presenter("get_forwarding_committees")
class GetForwardingCommittees(BasePresenter):
    """
    Get forwarded committees.
    """

    schema = get_forwarding_committees_schema

    def get_result(self) -> Any:

        # check permission
        if not has_perm(
            self.sql,
            self.user_id,
            Permissions.Motion.CAN_MANAGE_METADATA,
            self.data["meeting_id"],
        ):
            msg = "You are not allowed to perform presenter get_forwarding_committees"
            msg += f" Missing permission: {Permissions.Motion.CAN_MANAGE_METADATA}"
            raise PermissionDenied(msg)

        meeting = self.sql.get(
            "meeting",
            self.data["meeting_id"],
            ["committee_id"],
        ) or {}

        if not meeting.get("committee_id"):
            return []

        committee = self.sql.get(
            "committee",
            meeting["committee_id"],
            ["receive_forwardings_from_committee_ids"],
        ) or {}

        if not committee.get("receive_forwardings_from_committee_ids"):
            return []

        result = []
        for committee_id in committee["receive_forwardings_from_committee_ids"]:
            committee_data = self.sql.get(
                "committee", committee_id, ["name"]
            ) or {}
            if committee_data.get("name"):
                result.append(committee_data["name"])
        return result
