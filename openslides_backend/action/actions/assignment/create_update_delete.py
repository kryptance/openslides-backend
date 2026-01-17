from typing import Any

from ....models.models import Assignment
from ....permissions.permissions import Permissions
from ....shared.schema import id_list_schema
from ...action_set import ActionSet
from ...generics.update import UpdateAction
from ...mixins.create_action_with_dependencies import CreateActionWithDependencies
from ...util.default_schema import DefaultSchema
from ...util.register import register_action_set
from ..agenda_item.agenda_creation import (
    CreateActionWithAgendaItemMixin,
    agenda_creation_properties,
)
from ..agenda_item.create import AgendaItemCreate
from ..list_of_speakers.create import ListOfSpeakersCreate
from ..list_of_speakers.list_of_speakers_creation import (
    CreateActionWithListOfSpeakersMixin,
)
from ..meeting_mediafile.attachment_mixin import AttachmentMixin
from ..speaker.create import SpeakerCreateAction


class AssignmentCreate(
    AttachmentMixin,
    CreateActionWithDependencies,
    CreateActionWithAgendaItemMixin,
    CreateActionWithListOfSpeakersMixin,
):
    dependencies = [AgendaItemCreate, ListOfSpeakersCreate]


class AssignmentUpdate(AttachmentMixin, UpdateAction):
    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        if instance.get("phase") == "voting":
            assignment = self.sql.get(
                "assignment",
                instance["id"],
                ["meeting_id", "candidate_ids", "list_of_speakers_id", "phase"],
            ) or {}
            if (
                assignment.get("phase") != "voting"
                and (candidate_ids := assignment.get("candidate_ids"))
            ):
                meeting = self.sql.get(
                    "meeting",
                    assignment["meeting_id"],
                    ["assignment_poll_add_candidates_to_list_of_speakers"],
                ) or {}
                if meeting.get("assignment_poll_add_candidates_to_list_of_speakers"):
                    list_of_speakers = self.sql.get(
                        "list_of_speakers",
                        assignment["list_of_speakers_id"],
                        ["speaker_ids"],
                    ) or {}
                    speaker_ids = list_of_speakers.get("speaker_ids", [])
                    speakers = self.sql.get_many(
                        "speaker", speaker_ids, ["meeting_user_id"]
                    ) if speaker_ids else {}
                    pre_existing_speakers_meeting_user_ids = [
                        speaker["meeting_user_id"]
                        for speaker in speakers.values()
                    ]
                    candidates = self.sql.get_many(
                        "assignment_candidate", candidate_ids, ["meeting_user_id"]
                    ) if candidate_ids else {}
                    payloads = [
                        {
                            "list_of_speakers_id": assignment["list_of_speakers_id"],
                            "meeting_user_id": candidate["meeting_user_id"],
                        }
                        for candidate in candidates.values()
                        if candidate["meeting_user_id"]
                        not in pre_existing_speakers_meeting_user_ids
                    ]
                    for payload in payloads:
                        self.execute_other_action(SpeakerCreateAction, [payload])
        return super().update_instance(instance)


@register_action_set("assignment")
class AssignmentActionSet(ActionSet):
    """
    Actions to create, update and delete assignments.
    """

    model = Assignment()
    create_schema = DefaultSchema(Assignment()).get_create_schema(
        required_properties=["title", "meeting_id"],
        optional_properties=[
            "description",
            "open_posts",
            "phase",
            "default_poll_description",
            "number_poll_candidates",
            "tag_ids",
        ],
        additional_optional_fields={
            **agenda_creation_properties,
            "attachment_mediafile_ids": id_list_schema,
        },
    )
    update_schema = DefaultSchema(Assignment()).get_update_schema(
        optional_properties=[
            "title",
            "description",
            "open_posts",
            "phase",
            "default_poll_description",
            "number_poll_candidates",
            "tag_ids",
        ],
        additional_optional_fields={"attachment_mediafile_ids": id_list_schema},
    )
    delete_schema = DefaultSchema(Assignment()).get_delete_schema()
    permission = Permissions.Assignment.CAN_MANAGE

    CreateActionClass = AssignmentCreate
    UpdateActionClass = AssignmentUpdate
