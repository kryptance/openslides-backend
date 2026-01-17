from typing import Any

from ...shared.patterns import fqid_from_collection_and_id
from ..action import Action


class UpdateAction(Action):
    """
    Generic update action.

    Uses direct SQL for writing via self.sql.update().
    The PostgreSQL transaction ensures consistency.
    """

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        # Primary instance manipulation for defaults and extra fields.
        instance = self.update_instance(instance)
        self.apply_instance(instance)

        self.validate_relation_fields(instance)

        return instance

    def write_instance(self, instance: dict[str, Any]) -> None:
        """
        Writes one instance to the database via direct SQL UPDATE.
        Tracks the fqid for history.
        """
        fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])
        fields = {
            k: v for k, v in instance.items() if k != "id" and not k.startswith("meta_")
        }
        if not fields:
            return

        # Direct SQL UPDATE
        self.sql.update(self.model.collection, instance["id"], fields)

        # Track for history
        self.updated_fqids.add(fqid)
