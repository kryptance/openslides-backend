from typing import Any

from ....shared.exceptions import ActionException
from ....shared.filters import And, FilterOperator
from ...action import Action

ONE_ORGANIZATION_ID = 1


class ChatEnabledMixin(Action):
    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        organization = self.sql.get(
            "organization", ONE_ORGANIZATION_ID,
            ["enable_chat"],
            lock_result=False,
        ) or {}

        if not organization.get("enable_chat"):
            raise ActionException("Chat is not enabled.")
        return instance


class CheckUniqueNameMixin(Action):
    def check_name_unique(self, instance: dict[str, Any]) -> None:
        meeting_id = self.get_meeting_id(instance)
        name_exists = self.sql.exists(
            self.model.collection,
            And(
                FilterOperator("name", "=", instance["name"]),
                FilterOperator("meeting_id", "=", meeting_id),
            ),
        )
        if name_exists:
            raise ActionException("The name of a chat group must be unique.")
