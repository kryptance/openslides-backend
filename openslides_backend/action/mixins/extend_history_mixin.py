from typing import Any

from openslides_backend.models.fields import GenericRelationField, RelationField
from openslides_backend.shared.patterns import (
    collection_and_id_from_fqid,
    fqid_from_collection_and_id,
)

from ..action import Action
from ..generics.delete import DeleteAction


class ExtendHistoryMixin(Action):
    """
    Dynamic mixin class to extend the history of some related object with the entry of this one.
    Adds the related object's fqid to the updated_fqids set for history tracking.
    """

    extend_history_to: str

    def write_instance(self, instance: dict[str, Any]) -> None:
        # Call parent write_instance first
        super().write_instance(instance)

        # Extend history to related object
        field = self.model.get_field(self.extend_history_to)
        fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])

        # Check if model is pending deletion
        is_deleted = fqid in DeleteAction._pending_delete_fqids
        if is_deleted:
            return

        collection, id_ = collection_and_id_from_fqid(fqid)
        model = self.sql.get(collection, id_, [self.extend_history_to]) or {}
        value = model.get(self.extend_history_to)
        if not value:
            return

        # Add related object's fqid to updated_fqids for history tracking
        if isinstance(field, GenericRelationField):
            self.updated_fqids.add(value)  # value is already an fqid
        elif isinstance(field, RelationField):
            related_fqid = fqid_from_collection_and_id(field.get_target_collection(), value)
            self.updated_fqids.add(related_fqid)
        else:
            raise TypeError(f"Invalid related field type: {type(field)}")
