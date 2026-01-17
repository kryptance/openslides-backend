from collections.abc import Callable
from typing import Any

from openslides_backend.action.mixins.extend_history_mixin import ExtendHistoryMixin

from ....models.models import Poll
from ....shared.exceptions import ActionException, VoteServiceException
from ...generics.update import UpdateAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ...util.typing import ActionData
from .mixins import PollHistoryMixin, PollPermissionMixin, StopControl


@register_action("poll.stop")
class PollStopAction(
    ExtendHistoryMixin,
    StopControl,
    UpdateAction,
    PollPermissionMixin,
    PollHistoryMixin,
):
    """
    Action to stop a poll.
    """

    model = Poll()
    schema = DefaultSchema(Poll()).get_update_schema()
    poll_history_information = "stopped"
    extend_history_to = "content_object_id"

    def prefetch(self, action_data: ActionData) -> None:
        poll_ids = list({instance["id"] for instance in action_data})
        polls = self.sql.get_many(
            "poll",
            poll_ids,
            [
                "content_object_id",
                "meeting_id",
                "state",
                "voted_ids",
                "pollmethod",
                "global_option_id",
                "entitled_group_ids",
            ],
        )
        meeting_ids = list({poll["meeting_id"] for poll in polls.values()})
        # Prefetch meetings
        self.sql.get_many(
            "meeting",
            meeting_ids,
            [
                "poll_couple_countdown",
                "poll_countdown_id",
                "users_enable_vote_weight",
                "vote_ids",
            ],
        )
        # Prefetch groups
        group_ids = list(
            {
                group_id
                for poll in polls.values()
                for group_id in poll.get("entitled_group_ids", [])
            }
        )
        groups = self.sql.get_many("group", group_ids, ["meeting_user_ids"])
        # Prefetch meeting_users
        meeting_user_ids = list(
            {
                meeting_user_id
                for group in groups.values()
                for meeting_user_id in group.get("meeting_user_ids", [])
            }
        )
        meeting_users = self.sql.get_many("meeting_user", meeting_user_ids, ["user_id"])
        # Prefetch users
        user_ids = list({mu["user_id"] for mu in meeting_users.values()})
        self.sql.get_many(
            "user",
            user_ids,
            ["poll_voted_ids", "delegated_vote_ids", "vote_ids"],
        )

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        poll = self.sql.get(
            self.model.collection,
            instance["id"],
            ["state", "meeting_id", "voted_ids"],
        ) or {}
        if poll.get("state") != Poll.STATE_STARTED:
            raise ActionException(
                f"Cannot stop poll {instance['id']}, because it is not in state started."
            )
        instance["state"] = Poll.STATE_FINISHED
        self.on_stop(instance)
        return instance

    def get_on_success(self, action_data: ActionData) -> Callable[[], None]:
        def on_success() -> None:
            for instance in action_data:
                try:
                    self.vote_service.clear(instance["id"])
                except VoteServiceException as e:
                    self.logger.error(f"Error clearing vote {instance['id']}: {str(e)}")

        return on_success
