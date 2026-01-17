from typing import Any

from ....models.models import AssignmentCandidate
from ....shared.exceptions import ActionException
from ...mixins.create_action_with_inferred_meeting import (
    CreateActionWithInferredMeeting,
)
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ...util.typing import ActionData
from .mixins import PermissionMixin


@register_action("assignment_candidate.create")
class AssignmentCandidateCreate(PermissionMixin, CreateActionWithInferredMeeting):
    """
    Action to create an assignment candidate.
    """

    model = AssignmentCandidate()
    schema = DefaultSchema(AssignmentCandidate()).get_create_schema(
        required_properties=["assignment_id", "meeting_user_id"],
    )
    history_information = "Candidate added"
    history_relation_field = "assignment_id"

    relation_field_for_meeting = "assignment_id"

    def prefetch(self, action_data: ActionData) -> None:
        assignment_ids = list(
            {
                instance["assignment_id"]
                for instance in action_data
                if instance.get("assignment_id")
            }
        )
        self.sql.get_many(
            "assignment",
            assignment_ids,
            ["meeting_id", "phase", "candidate_ids"],
        ) if assignment_ids else {}

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        instance = super().update_instance(instance)
        assignment = self.sql.get(
            "assignment", instance["assignment_id"],
            ["phase"],
        ) or {}
        if assignment.get("phase") == "finished":
            raise ActionException(
                "It is not permitted to add a candidate to a finished assignment!"
            )
        return instance
