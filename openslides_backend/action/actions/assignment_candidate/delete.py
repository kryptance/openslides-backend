from typing import Any

from ....models.models import AssignmentCandidate
from ....shared.exceptions import ActionException
from ...generics.delete import DeleteAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from .mixins import PermissionMixin


@register_action("assignment_candidate.delete")
class AssignmentCandidateDelete(PermissionMixin, DeleteAction):
    """
    Action to delete a assignment candidate.
    """

    model = AssignmentCandidate()
    schema = DefaultSchema(AssignmentCandidate()).get_delete_schema()
    history_information = "Candidate removed"
    history_relation_field = "assignment_id"

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        instance = super().update_instance(instance)
        if not self.internal:
            assignment_candidate = self.sql.get(
                self.model.collection, instance["id"],
                ["assignment_id"],
            ) or {}
            assignment = self.sql.get(
                "assignment", assignment_candidate["assignment_id"],
                ["phase", "meeting_id"],
                lock_result=False,
            ) or {}
            if assignment.get(
                "phase"
            ) == "finished" and not self.is_meeting_to_be_deleted(
                assignment.get("meeting_id", 0)
            ):
                raise ActionException(
                    "It is not permitted to remove a candidate from a finished assignment!"
                )
        return instance
