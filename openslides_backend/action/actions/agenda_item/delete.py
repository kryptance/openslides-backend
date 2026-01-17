from typing import Any

from ....models.models import AgendaItem
from ....permissions.permissions import Permissions
from ....shared.filters import FilterOperator
from ....shared.patterns import (
    collection_from_fqid,
    id_from_fqid,
)
from ...generics.delete import DeleteAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ..topic.delete import TopicDelete


@register_action("agenda_item.delete")
class AgendaItemDelete(DeleteAction):
    """
    Action to delete agenda items.
    """

    model = AgendaItem()
    schema = DefaultSchema(AgendaItem()).get_delete_schema()
    permission = Permissions.AgendaItem.CAN_MANAGE

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        """Deletes the topic before the relation handling can try to create a faulty update to it."""
        agenda_item = self.sql.get(
            self.model.collection, instance["id"],
            ["content_object_id"],
        ) or {}
        if content_object_fqid := agenda_item.get("content_object_id"):
            topic_id = id_from_fqid(content_object_fqid)
            # Check if topic still exists (not already deleted in this transaction)
            if collection_from_fqid(content_object_fqid) == "topic" and self.sql.exists(
                "topic", FilterOperator("id", "=", topic_id)
            ):
                self.execute_other_action(
                    TopicDelete,
                    [{"id": topic_id}],
                )
        return instance
