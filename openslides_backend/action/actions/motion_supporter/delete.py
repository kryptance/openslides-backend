from typing import Any

from ....models.models import MotionSupporter
from ...mixins.motion_meeting_user_delete import build_motion_meeting_user_delete_action
from ...util.register import register_action
from ...util.typing import ActionData
from .mixins import SupporterActionMixin

BaseClass: type = build_motion_meeting_user_delete_action(MotionSupporter)


@register_action("motion_supporter.delete")
class MotionSupporterDeleteAction(BaseClass, SupporterActionMixin):
    history_information = "Supporters changed"
    history_relation_field = "motion_id"

    def prefetch(self, action_data: ActionData) -> None:
        super().prefetch(action_data)
        if not self.internal:
            supporter_ids = [payload["id"] for payload in action_data]
            self.sql.get_many(
                "motion_supporter",
                supporter_ids,
                ["motion_id", "meeting_user_id", "meeting_id"],
            )

    def get_motion_id(self, instance: dict[str, Any]) -> int:
        supporter = self.sql.get(
            "motion_supporter", instance["id"], ["motion_id"]
        ) or {}
        return supporter["motion_id"]

    def get_meeting_user_id(self, instance: dict[str, Any]) -> int | None:
        supporter = self.sql.get(
            "motion_supporter", instance["id"], ["meeting_user_id"]
        ) or {}
        return supporter.get("meeting_user_id")

    def get_updated_instances(self, action_data: ActionData) -> ActionData:
        if self.internal:
            return action_data
        return self.check_action_data(action_data)
