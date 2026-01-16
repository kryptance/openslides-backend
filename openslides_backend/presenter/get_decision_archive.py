from typing import Any

import fastjsonschema

from ..shared.exceptions import PermissionDenied
from ..shared.filters import And, FilterOperator, Or
from ..shared.schema import optional_id_schema, schema_version
from .base import BasePresenter
from .presenter import register_presenter

get_decision_archive_schema = fastjsonschema.compile(
    {
        "$schema": schema_version,
        "type": "object",
        "title": "get_decision_archive",
        "description": "Get archived decisions from all configured meetings",
        "properties": {
            "meeting_id": optional_id_schema,
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "offset": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    }
)


@register_presenter("get_decision_archive")
class GetDecisionArchive(BasePresenter):
    """
    Returns archived decisions from all meetings with enable_decision_archive=True.
    Only main motions (no amendments) in states with publish_to_archive=True are returned.
    Only accessible for logged-in users.
    """

    schema = get_decision_archive_schema

    def get_result(self) -> Any:
        # Check user is logged in
        if self.user_id == 0:
            raise PermissionDenied(
                "Anonymous users are not allowed to access the decision archive."
            )

        # Get optional filters from data
        meeting_id_filter = self.data.get("meeting_id") if self.data else None
        limit = self.data.get("limit", 100) if self.data else 100
        offset = self.data.get("offset", 0) if self.data else 0

        # Step 1: Find meetings with enable_decision_archive=True
        meeting_filter = FilterOperator("enable_decision_archive", "=", True)
        if meeting_id_filter:
            meeting_filter = And(
                meeting_filter,
                FilterOperator("id", "=", meeting_id_filter),
            )

        archive_meetings = self.datastore.filter(
            "meeting",
            meeting_filter,
            ["id", "name", "committee_id", "start_time", "end_time"],
            lock_result=False,
        )

        if not archive_meetings:
            return {"motions": [], "meetings": [], "total_count": 0}

        archive_meeting_ids = list(archive_meetings.keys())

        # Step 2: Find motion_states with publish_to_archive=True in those meetings
        state_filter = And(
            FilterOperator("publish_to_archive", "=", True),
            Or(
                *[
                    FilterOperator("meeting_id", "=", meeting_id)
                    for meeting_id in archive_meeting_ids
                ]
            ),
        )

        archive_states = self.datastore.filter(
            "motion_state",
            state_filter,
            ["id", "meeting_id", "name"],
            lock_result=False,
        )

        if not archive_states:
            return {
                "motions": [],
                "meetings": self._format_meetings(archive_meetings),
                "total_count": 0,
            }

        archive_state_ids = list(archive_states.keys())

        # Step 3: Find motions in those states with no lead_motion_id (not amendments)
        motion_filter = And(
            Or(
                *[
                    FilterOperator("state_id", "=", state_id)
                    for state_id in archive_state_ids
                ]
            ),
            FilterOperator("lead_motion_id", "=", None),
        )

        # Count total for pagination
        total_count = self.datastore.count(
            "motion",
            motion_filter,
            lock_result=False,
        )

        # Get motions with fields needed for display
        all_motions = self.datastore.filter(
            "motion",
            motion_filter,
            [
                "id",
                "meeting_id",
                "title",
                "number",
                "state_id",
                "category_id",
                "text",
                "reason",
                "created",
                "last_modified",
                "workflow_timestamp",
                "sequential_number",
            ],
            lock_result=False,
        )

        # Sort by workflow_timestamp (most recent first), then by id
        sorted_motions = sorted(
            all_motions.values(),
            key=lambda m: (-(m.get("workflow_timestamp") or 0), -m.get("id", 0)),
        )

        # Apply pagination
        paginated_motions = sorted_motions[offset : offset + limit]

        # Get category info for the motions
        category_ids = list(
            {m.get("category_id") for m in paginated_motions if m.get("category_id")}
        )
        categories = {}
        if category_ids:
            from ..services.database.interface import GetManyRequest

            categories_result = self.datastore.get_many(
                [GetManyRequest("motion_category", category_ids, ["id", "name", "prefix"])],
                lock_result=False,
            )
            categories = categories_result.get("motion_category", {})

        # Get committee info
        committee_ids = list(
            {m.get("committee_id") for m in archive_meetings.values() if m.get("committee_id")}
        )
        committees = {}
        if committee_ids:
            from ..services.database.interface import GetManyRequest

            committees_result = self.datastore.get_many(
                [GetManyRequest("committee", committee_ids, ["id", "name"])],
                lock_result=False,
            )
            committees = committees_result.get("committee", {})

        # Format result
        result_motions = []
        for motion in paginated_motions:
            motion_data = {
                "id": motion["id"],
                "meeting_id": motion["meeting_id"],
                "title": motion.get("title", ""),
                "number": motion.get("number", ""),
                "text": motion.get("text", ""),
                "reason": motion.get("reason", ""),
                "created": motion.get("created"),
                "workflow_timestamp": motion.get("workflow_timestamp"),
                "sequential_number": motion.get("sequential_number"),
            }

            # Add state info
            state = archive_states.get(motion.get("state_id"))
            if state:
                motion_data["state_name"] = state.get("name", "")

            # Add category info
            category_id = motion.get("category_id")
            if category_id and category_id in categories:
                category = categories[category_id]
                motion_data["category_name"] = category.get("name", "")
                motion_data["category_prefix"] = category.get("prefix", "")

            # Add meeting info
            meeting = archive_meetings.get(motion["meeting_id"])
            if meeting:
                motion_data["meeting_name"] = meeting.get("name", "")
                committee_id = meeting.get("committee_id")
                if committee_id and committee_id in committees:
                    motion_data["committee_name"] = committees[committee_id].get("name", "")

            result_motions.append(motion_data)

        return {
            "motions": result_motions,
            "meetings": self._format_meetings(archive_meetings, committees),
            "total_count": total_count,
        }

    def _format_meetings(
        self,
        meetings: dict[int, dict[str, Any]],
        committees: dict[int, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Format meetings for the response."""
        result = []
        for meeting_id, meeting in meetings.items():
            meeting_data = {
                "id": meeting_id,
                "name": meeting.get("name", ""),
                "start_time": meeting.get("start_time"),
                "end_time": meeting.get("end_time"),
            }
            committee_id = meeting.get("committee_id")
            if committee_id and committees and committee_id in committees:
                meeting_data["committee_name"] = committees[committee_id].get("name", "")
            result.append(meeting_data)
        return result
