from .base import BasePresenterTestCase


class TestGetDecisionArchive(BasePresenterTestCase):
    def test_anonymous_not_allowed(self) -> None:
        """Anonymous users should not be able to access the decision archive."""
        self.set_models({"user/1": {"username": "admin"}})
        # Anonymous request (user_id=0)
        self.anonymous = True
        status_code, data = self.request("get_decision_archive", {})
        self.assertEqual(status_code, 403)

    def test_empty_archive(self) -> None:
        """When no meetings have archive enabled, return empty results."""
        self.set_models(
            {
                "user/1": {"username": "testuser"},
                "meeting/1": {
                    "name": "Test Meeting",
                    "enable_decision_archive": False,
                    "is_active_in_organization_id": 1,
                },
            }
        )
        status_code, data = self.request("get_decision_archive", {})
        self.assertEqual(status_code, 200)
        self.assertEqual(data["motions"], [])
        self.assertEqual(data["meetings"], [])
        self.assertEqual(data["total_count"], 0)

    def test_archive_with_motions(self) -> None:
        """Test that motions in archive states from archive-enabled meetings are returned."""
        self.set_models(
            {
                "user/1": {"username": "testuser"},
                "committee/1": {"name": "Test Committee", "meeting_ids": [1]},
                "meeting/1": {
                    "name": "Test Meeting",
                    "committee_id": 1,
                    "enable_decision_archive": True,
                    "is_active_in_organization_id": 1,
                    "motion_ids": [1, 2],
                    "motion_state_ids": [1, 2],
                    "motion_workflow_ids": [1],
                },
                "motion_workflow/1": {
                    "name": "Test Workflow",
                    "meeting_id": 1,
                    "state_ids": [1, 2],
                    "first_state_id": 1,
                },
                "motion_state/1": {
                    "name": "Submitted",
                    "meeting_id": 1,
                    "workflow_id": 1,
                    "publish_to_archive": False,
                    "motion_ids": [1],
                },
                "motion_state/2": {
                    "name": "Accepted",
                    "meeting_id": 1,
                    "workflow_id": 1,
                    "publish_to_archive": True,
                    "motion_ids": [2],
                },
                "motion/1": {
                    "title": "Motion 1 - Not Archived",
                    "meeting_id": 1,
                    "state_id": 1,
                    "number": "M1",
                    "sequential_number": 1,
                },
                "motion/2": {
                    "title": "Motion 2 - Archived",
                    "meeting_id": 1,
                    "state_id": 2,
                    "number": "M2",
                    "sequential_number": 2,
                    "workflow_timestamp": 1700000000,
                },
            }
        )
        status_code, data = self.request("get_decision_archive", {})
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(len(data["motions"]), 1)
        self.assertEqual(data["motions"][0]["title"], "Motion 2 - Archived")
        self.assertEqual(data["motions"][0]["number"], "M2")
        self.assertEqual(data["motions"][0]["state_name"], "Accepted")
        self.assertEqual(data["motions"][0]["meeting_name"], "Test Meeting")

    def test_amendments_excluded(self) -> None:
        """Amendments (motions with lead_motion_id) should not appear in the archive."""
        self.set_models(
            {
                "user/1": {"username": "testuser"},
                "committee/1": {"name": "Test Committee", "meeting_ids": [1]},
                "meeting/1": {
                    "name": "Test Meeting",
                    "committee_id": 1,
                    "enable_decision_archive": True,
                    "is_active_in_organization_id": 1,
                    "motion_ids": [1, 2],
                    "motion_state_ids": [1],
                    "motion_workflow_ids": [1],
                },
                "motion_workflow/1": {
                    "name": "Test Workflow",
                    "meeting_id": 1,
                    "state_ids": [1],
                    "first_state_id": 1,
                },
                "motion_state/1": {
                    "name": "Accepted",
                    "meeting_id": 1,
                    "workflow_id": 1,
                    "publish_to_archive": True,
                    "motion_ids": [1, 2],
                },
                "motion/1": {
                    "title": "Main Motion",
                    "meeting_id": 1,
                    "state_id": 1,
                    "number": "M1",
                    "sequential_number": 1,
                    "amendment_ids": [2],
                },
                "motion/2": {
                    "title": "Amendment to M1",
                    "meeting_id": 1,
                    "state_id": 1,
                    "number": "M1-A1",
                    "sequential_number": 2,
                    "lead_motion_id": 1,
                },
            }
        )
        status_code, data = self.request("get_decision_archive", {})
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(len(data["motions"]), 1)
        self.assertEqual(data["motions"][0]["title"], "Main Motion")

    def test_filter_by_meeting(self) -> None:
        """Test that filtering by meeting_id works."""
        self.set_models(
            {
                "user/1": {"username": "testuser"},
                "committee/1": {"name": "Test Committee", "meeting_ids": [1, 2]},
                "meeting/1": {
                    "name": "Meeting 1",
                    "committee_id": 1,
                    "enable_decision_archive": True,
                    "is_active_in_organization_id": 1,
                    "motion_ids": [1],
                    "motion_state_ids": [1],
                    "motion_workflow_ids": [1],
                },
                "meeting/2": {
                    "name": "Meeting 2",
                    "committee_id": 1,
                    "enable_decision_archive": True,
                    "is_active_in_organization_id": 1,
                    "motion_ids": [2],
                    "motion_state_ids": [2],
                    "motion_workflow_ids": [2],
                },
                "motion_workflow/1": {
                    "name": "Workflow 1",
                    "meeting_id": 1,
                    "state_ids": [1],
                    "first_state_id": 1,
                },
                "motion_workflow/2": {
                    "name": "Workflow 2",
                    "meeting_id": 2,
                    "state_ids": [2],
                    "first_state_id": 2,
                },
                "motion_state/1": {
                    "name": "Accepted",
                    "meeting_id": 1,
                    "workflow_id": 1,
                    "publish_to_archive": True,
                    "motion_ids": [1],
                },
                "motion_state/2": {
                    "name": "Accepted",
                    "meeting_id": 2,
                    "workflow_id": 2,
                    "publish_to_archive": True,
                    "motion_ids": [2],
                },
                "motion/1": {
                    "title": "Motion from Meeting 1",
                    "meeting_id": 1,
                    "state_id": 1,
                    "number": "M1",
                    "sequential_number": 1,
                },
                "motion/2": {
                    "title": "Motion from Meeting 2",
                    "meeting_id": 2,
                    "state_id": 2,
                    "number": "M2",
                    "sequential_number": 1,
                },
            }
        )
        # Get all
        status_code, data = self.request("get_decision_archive", {})
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 2)

        # Filter by meeting 1
        status_code, data = self.request("get_decision_archive", {"meeting_id": 1})
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(data["motions"][0]["title"], "Motion from Meeting 1")

    def test_pagination(self) -> None:
        """Test that pagination works correctly."""
        motions_data = {}
        motion_ids = []
        for i in range(1, 6):
            motion_ids.append(i)
            motions_data[f"motion/{i}"] = {
                "title": f"Motion {i}",
                "meeting_id": 1,
                "state_id": 1,
                "number": f"M{i}",
                "sequential_number": i,
                "workflow_timestamp": 1700000000 + i,
            }

        self.set_models(
            {
                "user/1": {"username": "testuser"},
                "committee/1": {"name": "Test Committee", "meeting_ids": [1]},
                "meeting/1": {
                    "name": "Test Meeting",
                    "committee_id": 1,
                    "enable_decision_archive": True,
                    "is_active_in_organization_id": 1,
                    "motion_ids": motion_ids,
                    "motion_state_ids": [1],
                    "motion_workflow_ids": [1],
                },
                "motion_workflow/1": {
                    "name": "Test Workflow",
                    "meeting_id": 1,
                    "state_ids": [1],
                    "first_state_id": 1,
                },
                "motion_state/1": {
                    "name": "Accepted",
                    "meeting_id": 1,
                    "workflow_id": 1,
                    "publish_to_archive": True,
                    "motion_ids": motion_ids,
                },
                **motions_data,
            }
        )
        # First page with limit 2
        status_code, data = self.request(
            "get_decision_archive", {"limit": 2, "offset": 0}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 5)
        self.assertEqual(len(data["motions"]), 2)

        # Second page
        status_code, data = self.request(
            "get_decision_archive", {"limit": 2, "offset": 2}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_count"], 5)
        self.assertEqual(len(data["motions"]), 2)
