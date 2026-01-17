from collections.abc import Iterable
from typing import Any

from ...shared.interfaces.event import Event, EventType
from ...shared.patterns import fqid_from_collection_and_id
from ..action import Action


class UpdateAction(Action):
    """
    Generic update action.

    Uses direct SQL for writing via self.sql.update().
    The PostgreSQL transaction ensures consistency.
    """

    # Set to True to use direct SQL instead of events (default for new code)
    use_direct_sql: bool = True

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        # Primary instance manipulation for defaults and extra fields.
        instance = self.update_instance(instance)
        self.apply_instance(instance)

        self.validate_relation_fields(instance)

        return instance

    def create_events(self, instance: dict[str, Any]) -> Iterable[Event]:
        """
        Creates events for one instance of the current model.

        If use_direct_sql is True, this also writes directly to the database.
        Events are still generated for history tracking and backward compatibility.
        """
        fqid = fqid_from_collection_and_id(self.model.collection, instance["id"])
        fields = {
            k: v for k, v in instance.items() if k != "id" and not k.startswith("meta_")
        }
        if not fields:
            return

        # Direct SQL write if enabled
        if self.use_direct_sql:
            self.sql.update(self.model.collection, instance["id"], fields)

        yield self.build_event(EventType.Update, fqid, fields)
