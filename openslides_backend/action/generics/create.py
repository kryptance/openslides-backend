from typing import Any

from ...shared.patterns import fqid_from_collection_and_id
from ..action import Action
from ..util.typing import ActionData, ActionResultElement


class CreateAction(Action):
    """
    Generic create action.

    Uses direct SQL for writing via self.sql.insert().
    The PostgreSQL transaction ensures consistency.
    """

    def prepare_action_data(self, action_data: ActionData) -> ActionData:
        if not action_data:
            return action_data
        # Use sql.reserve_ids for direct SQL access
        new_ids = self.sql.reserve_ids(
            collection=self.model.collection, amount=len(list(action_data))
        )
        for instance, new_id in zip(action_data, new_ids):
            instance["id"] = new_id
        return action_data

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        # Primary instance manipulation for defaults and extra fields.
        instance = self.set_defaults(instance)

        instance["meta_new"] = True  # mark as a new model
        instance = self.update_instance(instance)
        self.apply_instance(instance)
        self.validate_relation_fields(instance)

        return instance

    def set_defaults(self, instance: dict[str, Any]) -> dict[str, Any]:
        for field in self.model.get_fields():
            if (
                field.own_field_name not in instance.keys()
                and field.default is not None
            ):
                instance[field.own_field_name] = field.default
        return instance

    def write_instance(self, instance: dict[str, Any]) -> None:
        """
        Writes one instance to the database via direct SQL INSERT.
        Tracks the fqid for history.
        """
        fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])

        # Clean up meta fields before writing
        write_instance = {
            k: v for k, v in instance.items() if not k.startswith("meta_")
        }

        # Direct SQL INSERT
        self.sql.insert(self.model.collection, write_instance, instance["id"])

        # Track for history
        self.created_fqids.add(fqid)

    def create_action_result_element(
        self, instance: dict[str, Any]
    ) -> ActionResultElement | None:
        """Returns the newly created id."""
        return {"id": instance["id"]}
