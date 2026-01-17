from typing import Any

from ....models.models import MotionCommentSection
from ....permissions.permissions import Permissions
from ....shared.exceptions import ActionException, ProtectedModelsException
from ....shared.patterns import id_from_fqid
from ...generics.delete import DeleteAction
from ...util.default_schema import DefaultSchema
from ...util.register import register_action


@register_action("motion_comment_section.delete")
class MotionCommentSectionDeleteAction(DeleteAction):
    """
    Delete Action with check for empty comments.
    """

    model = MotionCommentSection()
    schema = DefaultSchema(MotionCommentSection()).get_delete_schema()
    permission = Permissions.Motion.CAN_MANAGE

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().base_update_instance(instance)
        except ProtectedModelsException as e:
            comment_ids = [id_from_fqid(fqid) for fqid in e.fqids]
            comments = self.sql.get_many(
                "motion_comment", comment_ids, ["motion_id"],
                lock_result=False,
            ) if comment_ids else {}

            motions = {f'"{instance["motion_id"]}"' for instance in comments.values()}

            count = len(motions)
            motions_verbose = ", ".join(list(motions)[:3])
            if count > 3:
                motions_verbose += ", .."

            if count == 1:
                msg = f"This section has still comments in motion {motions_verbose}."
            else:
                msg = f"This section has still comments in motions {motions_verbose}."
            msg += " Please remove all comments before deletion."
            raise ActionException(msg)
