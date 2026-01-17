from ....shared.exceptions import PermissionDenied
from ...action import Action


class PermissionMixin(Action):
    def check_anonymous_and_user_in_meeting(self, meeting_id: int) -> None:
        self.assert_not_anonymous()
        user = self.sql.get(
            "user", self.user_id,
            ["meeting_ids"],
            lock_result=False,
        ) or {}
        if meeting_id not in user.get("meeting_ids", []):
            raise PermissionDenied("User not associated with meeting.")
