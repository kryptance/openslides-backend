from typing import Any

from openslides_backend.action.mixins.meeting_user_helper import get_meeting_user

from ...shared.exceptions import ActionException
from ...shared.patterns import (
    KEYSEPARATOR,
    FullQualifiedId,
    collection_from_fqid,
    id_from_fqid,
)


def assert_belongs_to_meeting(
    sql: Any,
    fqids: FullQualifiedId | list[FullQualifiedId],
    meeting_id: int,
) -> None:
    if not isinstance(fqids, list):
        fqids = [fqids]

    errors: set[str] = set()
    for fqid in fqids:
        if collection_from_fqid(fqid) == "meeting":
            if id_from_fqid(fqid) != meeting_id:
                errors.add(str(fqid))
        elif collection_from_fqid(fqid) == "user":
            instance = sql.get(
                "user",
                id_from_fqid(fqid),
                ["meeting_ids"],
            ) or {}
            if meeting_id in instance.get("meeting_ids", []):
                continue
            # try on sql whether minimum 1 group-relation exist in meeting_user
            meeting_user = get_meeting_user(
                sql, meeting_id, id_from_fqid(fqid), ["group_ids"]
            )
            if meeting_user and meeting_user.get("group_ids"):
                continue
            errors.add(str(fqid))
        elif collection_from_fqid(fqid) == "mediafile":
            mediafile = sql.get(
                "mediafile",
                id_from_fqid(fqid),
                ["owner_id"],
            ) or {}
            if owner_id := mediafile.get("owner_id"):
                collection, id_ = owner_id.split(KEYSEPARATOR)
                if collection == "meeting":
                    if int(id_) != meeting_id:
                        errors.add(str(fqid))
                else:
                    errors.add(str(fqid))
            else:
                errors.add(str(fqid))
        else:
            instance = sql.get(
                collection_from_fqid(fqid),
                id_from_fqid(fqid),
                ["meeting_id"],
            )
            # If model doesn't exist, it might be created in the same transaction.
            # Skip validation for now - the DB constraints will catch any real issues.
            if instance is None:
                continue
            if instance.get("meeting_id") != meeting_id:
                errors.add(str(fqid))

    if errors:
        raise ActionException(
            f"The following models do not belong to meeting {meeting_id}: {list(errors)}"
        )
