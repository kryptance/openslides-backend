from typing import cast

from openslides_backend.action.util.typing import ActionData

from ....models.models import Vote
from ...mixins.create_action_with_inferred_meeting import (
    CreateActionWithInferredMeeting,
)
from ...util.action_type import ActionType
from ...util.default_schema import DefaultSchema
from ...util.register import register_action


@register_action("vote.create", action_type=ActionType.BACKEND_INTERNAL)
class VoteCreate(CreateActionWithInferredMeeting):
    """
    Internal action to create a vote.
    """

    model = Vote()
    schema = DefaultSchema(Vote()).get_create_schema(
        required_properties=[
            "weight",
            "value",
            "option_id",
            "user_token",
        ],
        optional_properties=["delegated_user_id", "user_id"],
    )

    relation_field_for_meeting = "option_id"

    def prefetch(self, action_data: ActionData) -> None:
        option_ids = list({instance["option_id"] for instance in action_data})
        self.sql.get_many("option", option_ids, ["meeting_id", "vote_ids"])

        meeting_user_ids = list(
            {
                cast(int, instance.get(fname))
                for instance in action_data
                for fname in ("meeting_user_id", "delegated_meeting_user_id")
                if instance.get(fname)
            }
        )
        meeting_users = self.sql.get_many(
            "meeting_user",
            meeting_user_ids,
            ["id", "user_id", "vote_ids", "delegated_vote_ids"],
        )

        user_ids = list({mu["user_id"] for mu in meeting_users.values()})
        self.sql.get_many("user", user_ids, ["id", "poll_voted_ids"])
