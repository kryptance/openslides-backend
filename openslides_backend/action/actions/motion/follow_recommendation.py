from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openslides_backend.shared.typing import HistoryInformation

from ....models.models import Motion
from ....permissions.permissions import Permissions
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ...util.typing import ActionData
from .set_state import MotionSetStateAction


@register_action("motion.follow_recommendation")
class MotionFollowRecommendationAction(MotionSetStateAction):
    model = Motion()
    schema = DefaultSchema(Motion()).get_update_schema()
    permission = Permissions.Motion.CAN_MANAGE_METADATA

    def get_updated_instances(self, action_data: ActionData) -> ActionData:
        ids = [instance["id"] for instance in action_data]
        motions = self.sql.get_many(
            self.model.collection,
            ids,
            [
                "id",
                "recommendation_id",
                "recommendation_extension",
                "recommendation_extension_reference_ids",
            ],
        ) if ids else {}

        for motion in motions.values():
            if motion.get("recommendation_id"):
                yield motion

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        """
        If motion has a recommendation_id, set the state to it and
        set state_extension.
        """
        self.skip_state_graph_check = False
        recommendation_id = instance.pop("recommendation_id")
        instance["state_id"] = recommendation_id
        instance = super().update_instance(instance)
        recommendation = self.sql.get(
            "motion_state", recommendation_id,
            ["show_state_extension_field", "show_recommendation_extension_field"],
            lock_result=False,
        ) or {}
        recommendation_extension = instance.pop("recommendation_extension", None)
        recommendation_extension_reference_ids = instance.pop(
            "recommendation_extension_reference_ids", None
        )
        if (
            recommendation_extension is not None
            and recommendation.get("show_state_extension_field")
            and recommendation.get("show_recommendation_extension_field")
        ):
            instance["state_extension"] = recommendation_extension
            instance["state_extension_reference_ids"] = (
                recommendation_extension_reference_ids or []
            )
        instance["last_modified"] = datetime.now(ZoneInfo("UTC"))
        return instance

    def get_history_information(self) -> HistoryInformation | None:
        return self._get_state_history_information("state_id", "State")
