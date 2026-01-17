from typing import Any

from ....models.models import Meeting
from ....permissions.management_levels import OrganizationManagementLevel
from ....shared.exceptions import ActionException
from ....shared.util import ONE_ORGANIZATION_ID
from ...generics.update import UpdateAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action


@register_action("meeting.unarchive")
class MeetingUnarchive(UpdateAction):
    model = Meeting()
    schema = DefaultSchema(Meeting()).get_update_schema()
    permission = OrganizationManagementLevel.SUPERADMIN
    skip_archived_meeting_check = True

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        meeting = self.sql.get(
            self.model.collection, instance["id"],
            ["committee_id", "is_active_in_organization_id"],
        ) or {}
        if meeting.get("is_active_in_organization_id"):
            raise ActionException(f"Meeting {instance['id']} is not archived.")

        organization = self.sql.get(
            "organization", ONE_ORGANIZATION_ID,
            ["active_meeting_ids", "limit_of_meetings"],
        ) or {}
        if (
            limit_of_meetings := organization.get("limit_of_meetings", 0)
        ) and limit_of_meetings == len(organization.get("active_meeting_ids", [])):
            raise ActionException(
                f"You cannot unarchive the archived meeting, because you reached your limit of {limit_of_meetings} active meetings."
            )
        instance["is_active_in_organization_id"] = ONE_ORGANIZATION_ID
        instance["is_archived_in_organization_id"] = None
        return instance
