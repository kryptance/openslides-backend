from collections.abc import Iterable
from typing import Any, ClassVar, cast

from ...models.fields import BaseRelationField, OnDelete
from ...shared.exceptions import ActionException, ProtectedModelsException
from ...shared.interfaces.event import Event, EventType
from ...shared.patterns import (
    FullQualifiedId,
    collection_from_fqid,
    fqid_from_collection_and_id,
    id_from_fqid,
    transform_to_fqids,
)
from ..action import Action
from ..util.actions_map import actions_map
from ..util.typing import ActionData


class DeleteAction(Action):
    """
    Generic delete action.

    Uses direct SQL for deleting via self.sql.delete().
    The PostgreSQL transaction ensures consistency.

    Handles OnDelete behaviors:
    - CASCADE: Recursively deletes related models
    - PROTECT: Prevents deletion if related models exist
    - SET_NULL: Sets the relation field to NULL on related models
    """

    # Class-level sets to track deletions across cascading delete actions
    # These are reset at the start of each top-level delete action
    _pending_delete_fqids: ClassVar[set[str]] = set()
    _pending_protected_fqids: ClassVar[set[str]] = set()

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        """
        Takes care of on_delete handling.
        """
        this_fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])

        # Skip if already marked for deletion (prevents infinite loops in CASCADE)
        if this_fqid in DeleteAction._pending_delete_fqids:
            return instance
        DeleteAction._pending_delete_fqids.add(this_fqid)

        relevant_fields = [
            field.get_own_field_name() for field in self.model.get_relation_fields()
        ]
        # Fetch db instance with all relevant fields
        db_instance = self.sql.get(
            self.model.collection,
            instance["id"],
            relevant_fields,
        ) or {}

        # Update instance (by default this does nothing)
        instance = self.update_instance(instance)

        # Update instance and set relation fields to None.
        # Gather all delete actions with action data and also all models to be deleted
        delete_actions: list[tuple[FullQualifiedId, type[Action], ActionData]] = []
        for field_name, value in db_instance.items():
            if field_name == "id":
                continue
            field = cast(BaseRelationField, self.model.get_field(field_name))
            # Check on_delete.
            # Extract all foreign keys as fqids from the model
            foreign_fqids = transform_to_fqids(value, field.get_target_collection())
            if field.on_delete != OnDelete.SET_NULL:
                if field.on_delete == OnDelete.PROTECT:
                    protected_fqids = [
                        fqid
                        for fqid in foreign_fqids
                        if fqid not in DeleteAction._pending_protected_fqids
                    ]
                    if protected_fqids:
                        raise ProtectedModelsException(this_fqid, protected_fqids)
                else:
                    # case: field.on_delete == OnDelete.CASCADE
                    # Execute the delete action for all fqids
                    for fqid in foreign_fqids:
                        if fqid in DeleteAction._pending_delete_fqids:
                            # Skip models that are already tracked for deletion
                            continue
                        delete_action_class = actions_map.get(
                            f"{collection_from_fqid(fqid)}.delete"
                        )
                        if not delete_action_class:
                            raise ActionException(
                                f"Can't cascade the delete action to {collection_from_fqid(fqid)} "
                                "since no delete action was found."
                            )
                        # Assume that the delete action uses the standard action data
                        action_data = [{"id": id_from_fqid(fqid)}]
                        delete_actions.append((fqid, delete_action_class, action_data))
                        DeleteAction._pending_protected_fqids.add(fqid)
            elif field.is_view_field:
                # case: field.on_delete == OnDelete.SET_NULL
                instance[field_name] = None

        # Add additional relation models and execute all previously gathered delete actions
        # catch all protected models exception to gather all protected fqids
        all_protected_fqids: list[FullQualifiedId] = []
        for fqid, delete_action_class, delete_action_data in delete_actions:
            try:
                # Skip models that were already deleted (check database)
                collection = collection_from_fqid(fqid)
                id_ = id_from_fqid(fqid)
                if self.sql.get(collection, id_, ["id"]):
                    self.execute_other_action(delete_action_class, delete_action_data)
            except ProtectedModelsException as e:
                all_protected_fqids.extend(e.fqids)

        if all_protected_fqids:
            raise ProtectedModelsException(this_fqid, all_protected_fqids)

        return instance

    def create_events(self, instance: dict[str, Any]) -> Iterable[Event]:
        """
        Creates delete events for one instance of the current model.

        If use_direct_sql is True, this also deletes directly from the database.
        Events are still generated for history tracking and backward compatibility.
        """
        fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])

        # Direct SQL delete if enabled
        if self.use_direct_sql:
            self.sql.delete(self.model.collection, instance["id"])

        yield self.build_event(EventType.Delete, fqid)

    def is_meeting_to_be_deleted(self, meeting_id: int) -> bool:
        """
        Returns whether the given meeting was/will be deleted during this request or not.
        """
        fqid = fqid_from_collection_and_id("meeting", meeting_id)
        return fqid in DeleteAction._pending_delete_fqids

    def is_to_be_deleted(self, fqid: FullQualifiedId) -> bool:
        return fqid in DeleteAction._pending_delete_fqids

    @classmethod
    def reset_delete_tracking(cls) -> None:
        """
        Reset the deletion tracking sets. Should be called at the start of each request.
        """
        cls._pending_delete_fqids = set()
        cls._pending_protected_fqids = set()
