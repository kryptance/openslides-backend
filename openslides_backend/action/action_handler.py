from collections.abc import Callable, Iterable
from copy import deepcopy
from http import HTTPStatus
from typing import Any, TypeVar, cast

import fastjsonschema
from psycopg.errors import RaiseException

from openslides_backend.services.database.sql_helper import SqlHelper
from openslides_backend.services.postgresql.db_connection_handling import (
    get_new_os_conn,
)

from ..shared.exceptions import (
    ActionException,
    DatastoreLockedException,
    RelationException,
    View400Exception,
)
from ..shared.handlers.base_handler import BaseHandler
from ..shared.interfaces.env import Env
from ..shared.interfaces.logging import LoggingModule
from ..shared.interfaces.services import Services
from ..shared.otel import make_span
from ..shared.schema import schema_version
from . import actions  # noqa
from .relations.relation_manager import RelationManager
from .util.action_type import ActionType
from .util.actions_map import actions_map
from .util.typing import (
    ActionError,
    ActionResults,
    ActionsResponse,
    ActionsResponseResults,
    Payload,
    PayloadElement,
)

T = TypeVar("T")

action_data_schema = {
    "$schema": schema_version,
    "title": "Action data",
    "type": "array",
    "items": {"type": "object"},
}

payload_schema = fastjsonschema.compile(
    {
        "$schema": schema_version,
        "title": "Schema for action API",
        "description": "An array of actions",
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "action": {
                    "description": "Name of the action to be performed on the server",
                    "type": "string",
                    "minLength": 1,
                },
                "data": action_data_schema,
            },
            "required": ["action", "data"],
            "additionalProperties": False,
        },
    }
)


class ActionHandler(BaseHandler):
    """
    Action handler. It is the concrete implementation of Action interface.
    """

    MAX_RETRY = 3

    on_success: list[Callable[[], None]]

    def __init__(self, env: Env, services: Services, logging: LoggingModule) -> None:
        super().__init__(env, services, logging)
        self.on_success = []

    @classmethod
    def get_health_info(cls) -> Iterable[tuple[str, dict[str, Any]]]:
        """
        Returns name and development status of all actions.
        """
        for name in sorted(actions_map):
            action = actions_map[name]
            schema: dict[str, Any] = deepcopy(action_data_schema)
            schema["items"] = action.schema
            if action.is_singular:
                schema["maxItems"] = 1
            info = dict(
                schema=schema,
            )
            yield name, info

    def handle_request(
        self,
        payload: Payload,
        user_id: int,
        atomic: bool = True,
        internal: bool = False,
    ) -> ActionsResponse:
        """
        Takes payload and user id and handles this request by validating and
        parsing all actions. In the end it sends everything to the event store.
        """
        with make_span(self.env, "handle request"):
            with get_new_os_conn() as db_connection:
                self.db_connection = db_connection
                self.user_id = user_id
                self.internal = internal

                try:
                    payload_schema(payload)
                except fastjsonschema.JsonSchemaException as exception:
                    raise ActionException(exception.message)

            try:
                with get_new_os_conn() as conn:
                    self.sql = SqlHelper(conn, self.logging, self.env)
                    results: ActionsResponseResults = []
                    if atomic:
                        results = self.execute_actions(self.parse_actions, payload)
                    else:
                        for element in payload:
                            try:
                                result = self.execute_actions(
                                    lambda e: self.perform_action(e)[1],
                                    element,
                                )
                                results.append(result)
                            except ActionException as exception:
                                error = cast(ActionError, exception.get_json())
                                results.append(error)

                    # execute cleanup methods
                    for on_success in self.on_success:
                        on_success()

                    # Return action result
                    self.logger.info("Request was successful. Send response now.")
                    return ActionsResponse(
                        status_code=HTTPStatus.OK.value,
                        success=True,
                        message="Actions handled successfully",
                        results=results,
                    )
            except RaiseException as e:
                # This is raised at the end of transaction as the constraint trigger has to be initially deferred.
                raise RelationException(f"Relation violates required constraint: {e}")

    def execute_internal_action(self, action: str, data: dict[str, Any]) -> None:
        """Helper function to execute an internal action with user id -1."""
        self.handle_request(
            [
                {
                    "action": action,
                    "data": [data],
                }
            ],
            -1,
            internal=True,
        )

    def execute_actions(
        self,
        get_results: Callable[..., T],
        *args: Any,
    ) -> T:
        """Execute actions with retry logic for lock conflicts."""
        with make_span(self.env, "execute actions"):
            retries = 0
            while True:
                try:
                    return get_results(*args)
                except DatastoreLockedException as exception:
                    retries += 1
                    if retries >= self.MAX_RETRY:
                        raise ActionException(exception.message)

    def parse_actions(
        self, payload: Payload
    ) -> ActionsResponseResults:
        """
        Parses actions request send by client. Raises ActionException or
        PermissionDenied if something went wrong.
        """
        action_response_results: ActionsResponseResults = []
        relation_manager = RelationManager(self.sql)
        action_name_list = []
        for i, element in enumerate(payload):
            with make_span(self.env, f"parse action: {element['action']}"):
                action_name = element["action"]
                if (action := actions_map.get(action_name)) and action.is_singular:
                    if action_name in action_name_list:
                        exception = ActionException(
                            f"Action {action_name} may not appear twice in one request."
                        )
                        exception.action_error_index = i
                        raise exception
                    else:
                        action_name_list.append(action_name)
                try:
                    results = self.perform_action(element, relation_manager)
                except ActionException as exception:
                    exception.action_error_index = i
                    raise exception

                action_response_results.append(results)

        self.logger.debug("Actions performed successfully.")
        return action_response_results

    def perform_action(
        self,
        action_payload_element: PayloadElement,
        relation_manager: RelationManager | None = None,
    ) -> ActionResults | None:
        action_name = action_payload_element["action"]
        ActionClass = actions_map.get(action_name)
        # Actions cannot be accessed in the following three cases:
        # - they do not exist
        # - they are not public and the request is not internal
        # - they are backend internal and the backend is not in dev mode
        if (
            ActionClass is None
            or (ActionClass.action_type != ActionType.PUBLIC and not self.internal)
            or (
                ActionClass.action_type == ActionType.BACKEND_INTERNAL
                and not self.env.is_dev_mode()
            )
        ):
            raise View400Exception(f"Action {action_name} does not exist.")
        if not relation_manager:
            relation_manager = RelationManager(self.sql)

        self.logger.info(f"Performing action {action_name}.")
        action = ActionClass(
            self.services, relation_manager, self.logging, self.env,
            sql=self.sql,
        )
        action_data = deepcopy(action_payload_element["data"])

        try:
            with make_span(self.env, "action.perform"):
                _write_request, results = action.perform(
                    action_data, self.user_id, internal=self.internal
                )

            # add on_success routine
            if on_success := action.get_on_success(action_data):
                self.on_success.append(on_success)

            return results
        except ActionException as exception:
            self.logger.error(
                f"Error occured on index {action.index}: {exception.message}"
            )
            # -1: error which cannot be directly associated with a single action data
            if action.index > -1:
                exception.action_data_error_index = action.index
            if on_failure := action.get_on_failure(action_data):
                on_failure()
            raise exception
