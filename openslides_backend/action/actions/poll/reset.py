from collections.abc import Callable
from typing import Any

from openslides_backend.action.mixins.extend_history_mixin import ExtendHistoryMixin

from ....models.models import Poll
from ...generics.update import UpdateAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ...util.typing import ActionData
from ..option.set_auto_fields import OptionSetAutoFields
from ..vote.delete import VoteDelete
from .mixins import PollHistoryMixin, PollPermissionMixin


@register_action("poll.reset")
class PollResetAction(
    ExtendHistoryMixin, UpdateAction, PollPermissionMixin, PollHistoryMixin
):
    """
    Action to reset a poll.
    """

    model = Poll()
    schema = DefaultSchema(Poll()).get_update_schema()
    poll_history_information = "reset"
    extend_history_to = "content_object_id"

    def prefetch(self, action_data: ActionData) -> None:
        poll_ids = list({instance["id"] for instance in action_data})
        polls = self.sql.get_many(
            "poll",
            poll_ids,
            [
                "content_object_id",
                "meeting_id",
                "type",
                "voted_ids",
                "option_ids",
                "global_option_id",
            ],
        )
        meeting_ids = list({poll["meeting_id"] for poll in polls.values()})
        option_ids = [
            option_id
            for poll in polls.values()
            if poll.get("option_ids")
            for option_id in poll["option_ids"]
        ]
        # Prefetch meetings and options
        self.sql.get_many(
            "meeting",
            meeting_ids,
            ["is_active_in_organization_id", "name"],
        )
        self.sql.get_many("option", option_ids, ["vote_ids"])

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        instance["state"] = Poll.STATE_CREATED
        self.delete_all_votes(instance["id"])
        poll = self.sql.get(self.model.collection, instance["id"], ["type"]) or {}
        instance["is_pseudoanonymized"] = poll.get("type") == Poll.TYPE_PSEUDOANONYMOUS
        instance["voted_ids"] = []
        instance["entitled_users_at_stop"] = None
        instance["votesvalid"] = None
        instance["votesinvalid"] = None
        instance["votescast"] = None
        return instance

    def delete_all_votes(self, poll_id: int) -> None:
        option_ids = self._get_option_ids(poll_id)
        options = self._get_options(option_ids)
        for option_id in options:
            option = options[option_id]
            if option.get("vote_ids"):
                self._delete_votes(option["vote_ids"])
                self._clear_option_auto_fields(option_id)

    def _get_option_ids(self, poll_id: int) -> list[int]:
        poll = self.sql.get(
            self.model.collection, poll_id, ["option_ids", "global_option_id"]
        ) or {}
        option_ids = poll.get("option_ids", [])
        if poll.get("global_option_id"):
            option_ids.append(poll["global_option_id"])
        return option_ids

    def _get_options(self, option_ids: list[int]) -> dict[int, dict[str, Any]]:
        options: dict[int, dict[str, Any]] = self.sql.get_many(
            "option", option_ids, ["vote_ids"]
        )
        return options

    def _delete_votes(self, vote_ids: list[int]) -> None:
        action_data = []
        for id_ in vote_ids:
            action_data.append({"id": id_})
        self.execute_other_action(VoteDelete, action_data)

    def _clear_option_auto_fields(self, option_id: int) -> None:
        action_data = [
            {
                "id": option_id,
                "yes": "0.000000",
                "no": "0.000000",
                "abstain": "0.000000",
            }
        ]
        self.execute_other_action(OptionSetAutoFields, action_data)

    def get_on_success(self, action_data: ActionData) -> Callable[[], None]:
        def on_success() -> None:
            for instance in action_data:
                self.vote_service.clear(instance["id"])

        return on_success
