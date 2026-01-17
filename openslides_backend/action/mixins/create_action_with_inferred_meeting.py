from typing import Any, cast

from ...models.fields import BaseGenericRelationField, BaseRelationField
from ...shared.exceptions import ActionException
from ...shared.patterns import FullQualifiedId, fqid_from_collection_and_id
from ..generics.create import CreateAction


class CreateActionWithInferredMeetingMixin(CreateAction):
    """
    Mixin to automatically set the meeting_id on create if it's not given in the action data.
    The given relation_field_for_meeting must be a relation field.
    """

    relation_field_for_meeting: str

    def update_instance_with_meeting_id(
        self, instance: dict[str, Any]
    ) -> dict[str, Any]:
        # Allow passing meeting_id directly to avoid reading from DB
        if "meeting_id" not in instance:
            instance["meeting_id"] = self.get_meeting_id(instance)
        return instance

    def get_meeting_id(self, instance: dict[str, Any]) -> int:
        # Allow passing meeting_id directly to avoid reading from DB
        if "meeting_id" in instance:
            return instance["meeting_id"]

        field = self.model.get_field(self.relation_field_for_meeting)
        assert isinstance(field, BaseRelationField)
        id = instance[self.relation_field_for_meeting]
        if isinstance(field, BaseGenericRelationField):
            fqid = cast(FullQualifiedId, id)
            from ...shared.patterns import collection_from_fqid, id_from_fqid

            collection = collection_from_fqid(fqid)
            model_id = id_from_fqid(fqid)
        else:
            assert len(field.to) == 1
            collection = field.get_target_collection()
            model_id = id
        # Fetch meeting_id
        related_model = self.sql.get(
            collection,
            model_id,
            ["meeting_id"],
        ) or {}
        if not related_model.get("meeting_id"):
            raise ActionException(
                f"Referenced model in field {self.relation_field_for_meeting} has no meeting id."
            )
        return related_model["meeting_id"]


class CreateActionWithInferredMeeting(
    CreateActionWithInferredMeetingMixin, CreateAction
):
    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        return self.update_instance_with_meeting_id(instance)


def get_create_action_with_inferred_meeting(
    relation_field_for_meeting: str,
) -> type[CreateActionWithInferredMeeting]:
    """
    Shortcut to get a CreateAction class with inferred meeting.

        MyAction = get_create_action_with_inferred_meeting("foo")

    is equal to

        class MyAction(CreateActionWithInferredMeeting):
            relation_field_for_meeting = "foo"
    """
    return type(
        CreateActionWithInferredMeeting.__name__ + "_" + relation_field_for_meeting,
        (CreateActionWithInferredMeeting,),
        dict(relation_field_for_meeting=relation_field_for_meeting),
    )
