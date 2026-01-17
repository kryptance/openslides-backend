from collections.abc import Callable

from ....models.models import Poll
from ....shared.exceptions import VoteServiceException
from ...generics.delete import DeleteAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ...util.typing import ActionData
from .mixins import PollHistoryMixin, PollPermissionMixin


@register_action("poll.delete")
class PollDelete(DeleteAction, PollPermissionMixin, PollHistoryMixin):
    """
    Action to delete polls.
    """

    model = Poll()
    schema = DefaultSchema(Poll()).get_delete_schema()
    poll_history_information = "deleted"

    def prefetch(self, action_data: ActionData) -> None:
        poll_ids = list({instance["id"] for instance in action_data})
        poll_result = self.sql.get_many(
            "poll", poll_ids,
            [
                "content_object_id",
                "meeting_id",
                "entitled_group_ids",
                "voted_ids",
                "option_ids",
                "global_option_id",
                "projection_ids",
                "state",
            ],
            use_changed_models=False,
        ) if poll_ids else {}
        polls = poll_result.values()
        self.started_polls = [
            id_
            for id_, poll in poll_result.items()
            if poll.get("state") == "started"
        ]
        meeting_ids = list({poll["meeting_id"] for poll in polls})
        group_ids = list(
            {
                group_id
                for poll in polls
                for group_id in poll.get("entitled_group_ids", ())
            }
        )
        option_ids = [
            option_id
            for poll in polls
            if poll.get("option_ids")
            for option_id in poll["option_ids"]
        ]
        if meeting_ids:
            self.sql.get_many(
                "meeting", meeting_ids,
                [
                    "is_active_in_organization_id",
                    "name",
                    "option_ids",
                    "poll_ids",
                ],
                use_changed_models=False,
            )
        if option_ids:
            self.sql.get_many(
                "option", option_ids,
                [
                    "meeting_id",
                    "vote_ids",
                    "content_object_id",
                    "poll_id",
                    "used_as_global_option_in_poll_id",
                    "vote_ids",
                ],
                use_changed_models=False,
            )
        if group_ids:
            self.sql.get_many(
                "group", group_ids,
                ["poll_ids"],
                use_changed_models=False,
            )

    def get_on_success(self, action_data: ActionData) -> Callable[[], None]:
        def on_success() -> None:
            for instance in action_data:
                if (id_ := instance["id"]) in self.started_polls:
                    try:
                        self.vote_service.clear(id_)
                    except VoteServiceException as e:
                        self.logger.error(f"Error clearing vote {id_}: {str(e)}")

        return on_success
