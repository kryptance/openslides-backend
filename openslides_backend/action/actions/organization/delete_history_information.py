from typing import Any

from ....models.models import Organization
from ....permissions.management_levels import OrganizationManagementLevel
from ...action import Action
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ..history_position.delete import HistoryPositionDelete


@register_action("organization.delete_history_information")
class DeleteHistoryInformation(Action):
    """
    Action to delete history information.
    """

    model = Organization()
    schema = DefaultSchema(Organization()).get_update_schema()
    skip_archived_meeting_check = True
    permission = OrganizationManagementLevel.CAN_MANAGE_ORGANIZATION

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        all_positions = self.sql.get_all("history_position", ["id"])
        self.execute_other_action(
            HistoryPositionDelete, [{"id": id_} for id_ in all_positions]
        )
        return instance

    def write_instance(self, instance: dict[str, Any]) -> None:
        """Writes are delegated to execute_other_action."""
        pass
