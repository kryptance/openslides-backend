from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar, cast

import fastjsonschema
from psycopg.types.json import Jsonb

from openslides_backend.shared.base_service_provider import BaseServiceProvider

from ..models.base import Model, model_registry
from ..models.fields import BaseRelationField, GenericRelationField
from ..models.models import HistoryEntry
from ..permissions.management_levels import (
    CommitteeManagementLevel,
    OrganizationManagementLevel,
)
from ..permissions.permission_helper import has_organization_management_level, has_perm
from ..permissions.permissions import Permission
from ..presenter.base import BasePresenter
from ..services.database.interface import Database
from ..services.database.sql_helper import SqlHelper
from ..shared.exceptions import (
    ActionException,
    AnonymousNotAllowed,
    MissingPermission,
    PermissionDenied,
)
from ..shared.history_events import calculate_history_event_payloads
from ..shared.interfaces.env import Env
from ..shared.interfaces.logging import LoggingModule
from ..shared.interfaces.services import Services
from ..shared.interfaces.write_request import WriteRequest
from ..shared.otel import make_span
from ..shared.patterns import (
    FullQualifiedId,
    collection_from_fqid,
    fqid_and_field_from_fqfield,
    fqid_from_collection_and_id,
    id_from_fqid,
    transform_to_fqids,
)
from ..shared.typing import HistoryInformation
from .relations.relation_manager import RelationManager, RelationUpdates
from .relations.typing import FieldUpdateElement, ListUpdateElement
from .util.action_type import ActionType
from .util.assert_belongs_to_meeting import assert_belongs_to_meeting
from .util.typing import ActionData, ActionResultElement, ActionResults

HISTORY_MODELS = list(
    cast(GenericRelationField, HistoryEntry().get_field("model_id")).to.keys()
)


class SchemaProvider(type):
    """
    Metaclass to provide pre-compiled JSON schemas for faster validation.
    """

    def __new__(cls, name, bases, attrs):  # type: ignore
        schema = attrs.get("schema")
        if schema is not None:
            attrs["schema_validator"] = fastjsonschema.compile(schema)
        return super().__new__(cls, name, bases, attrs)


ORIGINAL_INSTANCES_FLAG = "_original_instances"


def original_instances(method: Callable) -> Callable:
    """
    Marker decorator for get_updated_instances to indicate that the method returns the original
    instances from the action data in the same order. Must be set to create action result.
    """
    setattr(method, ORIGINAL_INSTANCES_FLAG, True)
    return method


T = TypeVar("T", bound=WriteRequest)


class Action(BaseServiceProvider, metaclass=SchemaProvider):
    """
    Base class for an action.
    """

    name: str
    model: Model
    schema: dict
    schema_validator: Callable[[dict[str, Any]], None]

    is_singular: bool = False
    action_type: ActionType = ActionType.PUBLIC
    permission: Permission | OrganizationManagementLevel | None = None
    permission_model: Model | None = None
    permission_id: str | None = None
    skip_archived_meeting_check: bool = False
    use_meeting_ids_for_archived_meeting_check: bool = False
    history_information: str | None = None
    history_relation_field: str | None = None
    add_self_history_information: bool = False
    own_history_information_first: bool = False

    relation_manager: RelationManager
    sql: SqlHelper  # Direct SQL access for reading and writing

    action_data: ActionData
    instances: list[dict[str, Any]]
    results: ActionResults
    cascaded_actions_history: HistoryInformation
    internal: bool

    # Track which models are created/updated/deleted for history
    created_fqids: set[str]
    updated_fqids: set[str]
    deleted_fqids: set[str]

    def __init__(
        self,
        services: Services,
        datastore: Database,
        relation_manager: RelationManager,
        logging: LoggingModule,
        env: Env,
        skip_archived_meeting_check: bool | None = None,
        use_meeting_ids_for_archived_meeting_check: bool | None = None,
        sql: SqlHelper | None = None,
    ) -> None:
        # Keep datastore reference for backward compatibility
        self.datastore = datastore
        # Initialize SqlHelper if provided, otherwise create from datastore connection
        if sql is not None:
            self._sql = sql
        else:
            self._sql = SqlHelper(datastore.connection, logging, env)
        super().__init__(services, self._sql, logging)
        self.relation_manager = relation_manager
        self.logger = logging.getLogger(__name__)
        self.env = env
        if skip_archived_meeting_check is not None:
            self.skip_archived_meeting_check = skip_archived_meeting_check
        if use_meeting_ids_for_archived_meeting_check is not None:
            self.use_meeting_ids_for_archived_meeting_check = (
                use_meeting_ids_for_archived_meeting_check
            )
        self.results = []
        self.cascaded_actions_history = {}
        # Initialize tracking sets for history
        self.created_fqids = set()
        self.updated_fqids = set()
        self.deleted_fqids = set()

    def perform(
        self,
        action_data: ActionData,
        user_id: int,
        internal: bool = False,
        is_sub_call: bool = False,
    ) -> tuple[WriteRequest | None, ActionResults | None]:
        """
        Entrypoint to perform the action.
        """
        self.user_id = user_id
        self.index = 0
        self.internal = internal
        self.is_sub_call = is_sub_call

        # prefetch as much data as possible
        self.prefetch(action_data)

        for i, instance in enumerate(action_data):
            self.validate_instance(instance)
            cast(list[dict[str, Any]], action_data)[i] = self.validate_fields(instance)
            self.check_for_archived_meeting(instance)
            # perform permission check not for internal requests or backend_internal actions
            if not internal and self.action_type != ActionType.BACKEND_INTERNAL:
                try:
                    self.check_permissions(instance)
                except MissingPermission as e:
                    msg = f"You are not allowed to perform action {self.name}."
                    e.message = msg + " " + e.message
                    raise e
            self.index += 1
        self.index = -1

        action_data = self.prepare_action_data(action_data)
        self.action_data = deepcopy(action_data)
        self.instances = list(self.get_updated_instances(action_data))
        is_original_instances = hasattr(
            self.get_updated_instances, ORIGINAL_INSTANCES_FLAG
        )
        for instance in self.instances:
            # only increment index if the instances which are iterated here are the
            # same as the ones from the action data list (meaning get_updated_instances was
            # not overridden)
            if is_original_instances:
                self.index += 1

            instance = self.base_update_instance(instance)

            # Handle relation updates (writes directly to DB, tracks updated fqids)
            self.handle_relation_updates(instance)

            # Perform the main write operation (implemented in generic actions)
            self.write_instance(instance)

            if is_original_instances:
                result = self.create_action_result_element(instance)
                self.results.append(result)

        # Write history directly to database
        write_request = self.write_history()
        # by default, for actions which changed the updated instances, just return None
        if not is_original_instances and not self.results:
            return (write_request, None)

        return (write_request, self.results)

    def prefetch(self, action_data: ActionData) -> None:
        """
        Implement in subclasses to prefetch data for the action.
        """

    def check_permissions(self, instance: dict[str, Any]) -> None:
        """
        Checks permission by requesting permission service or using internal check.
        """
        if self.permission:
            if isinstance(self.permission, OrganizationManagementLevel):
                if has_organization_management_level(
                    self.sql,
                    self.user_id,
                    cast(OrganizationManagementLevel, self.permission),
                ):
                    return
                raise MissingPermission(self.permission)
            elif isinstance(self.permission, CommitteeManagementLevel):
                """
                set permission in class to: permission = CommitteeManagementLevel.CAN_MANAGE
                A specialized realisation see in create_update_permissions_mixin.py
                """
                raise NotImplementedError()
            else:
                meeting_id = self.get_meeting_id(instance)
                if has_perm(
                    self.sql,
                    self.user_id,
                    cast(Permission, self.permission),
                    meeting_id,
                ):
                    return
                raise MissingPermission(self.permission)

        msg = f"You are not allowed to perform action {self.name}."
        raise PermissionDenied(msg)

    def check_for_archived_meeting(self, instance: dict[str, Any]) -> None:
        """Do not allow changing any data in an archived meeting"""
        if self.skip_archived_meeting_check:
            return
        try:
            if self.use_meeting_ids_for_archived_meeting_check:
                meeting_ids = instance["meeting_ids"]
            else:
                meeting_ids = [self.get_meeting_id(instance)]
        except AttributeError:
            raise ActionException(
                f"get meeting failed Action: {self.name}. Perhaps you want to use skip_archived_meeting_checks = True attribute"
            )
        meetings = self.sql.get_many(
            "meeting", meeting_ids, ["id", "is_active_in_organization_id", "name"]
        ) if meeting_ids else {}
        for meeting in meetings.values():
            if not meeting.get("is_active_in_organization_id"):
                raise ActionException(
                    f'Meeting {meeting.get("name", "")}/{meeting["id"]} cannot be changed, because it is archived.'
                )

    def assert_not_anonymous(self) -> None:
        """
        Checks if the request user is the Anonymous and raises an error if it is.
        """
        if self.auth.is_anonymous(self.user_id):
            raise AnonymousNotAllowed(self.name)

    def get_meeting_id(self, instance: dict[str, Any]) -> int:
        """
        Returns the meeting_id, either directly from the instance or from the database.
        Must be overwritten if no meeting_id is present in either!
        """
        if instance.get("meeting_id"):
            return instance["meeting_id"]
        else:
            model = self.model
            if self.permission_model:
                model = self.permission_model
            identifier = "id"
            if self.permission_id:
                identifier = self.permission_id
            db_instance = self.sql.get(
                model.collection,
                instance[identifier],
                ["meeting_id"],
            ) or {}
            return db_instance["meeting_id"]

    @original_instances
    def get_updated_instances(self, action_data: ActionData) -> ActionData:
        """
        By default this does nothing. Override in subclasses to adjust the updates
        to all instances of the action data. You can only update instances of the model
        of this action. If overridden and not decorated with @original_instances, no
        action results will be created.
        If needed, this can also be used to do additional validation on the whole
        action data.
        """
        yield from action_data

    def prepare_action_data(self, action_data: ActionData) -> ActionData:
        """
        By default this does nothing.
        Override in subclass to pre_get ids.
        """
        return action_data

    def validate_instance(self, instance: dict[str, Any]) -> None:
        """
        Validates one instance of the action data according to schema class attribute.
        """
        try:
            type(self).schema_validator(
                {
                    # fmt: off
                    field:
                        int(value.timestamp()) if isinstance(value, datetime)
                        else int(value.total_seconds()) if isinstance(value, timedelta)
                        else str(value) if isinstance(value, Decimal)
                        else value.obj if isinstance(value, Jsonb)
                        else value
                    for field, value in instance.items()
                    # fmt: on
                }
            )
        except fastjsonschema.JsonSchemaException as exception:
            raise ActionException(f"Action {self.name}: " + exception.message)

    def base_update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        """
        Updates one instance of the action data. This can be overridden by custom
        action classes.
        """
        return self.update_instance(instance)

    def update_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        """
        Updates one instance of the action data. This can be overridden by custom
        action classes. Meant to be called inside base_update_instance.
        """
        return instance

    def handle_relation_updates(
        self,
        instance: dict[str, Any],
    ) -> None:
        """
        Handles relation updates by writing directly to DB and tracking updated fqids.
        """
        relation_updates = self.relation_manager.get_relation_updates(
            self.model, instance, self.name
        )
        self.handle_relation_updates_helper(relation_updates)

    def handle_relation_updates_helper(
        self,
        relation_updates: RelationUpdates,
    ) -> None:
        for fqfield, data in relation_updates.items():
            fqid, field = fqid_and_field_from_fqfield(fqfield)
            collection = collection_from_fqid(fqid)
            id_ = id_from_fqid(fqid)

            if data["type"] in ("add", "remove"):
                data = cast(FieldUpdateElement, data)
                self.sql.update(collection, id_, {field: data["value"]})
            elif data["type"] == "list_update":
                data = cast(ListUpdateElement, data)
                if data["add"]:
                    self.sql.add_to_list(collection, id_, field, data["add"])
                if data["remove"]:
                    self.sql.remove_from_list(collection, id_, field, data["remove"])

            # Track this fqid as updated for history
            self.updated_fqids.add(fqid)

    def write_instance(self, instance: dict[str, Any]) -> None:
        """
        Writes one instance to the database. To be overridden in subclasses (CreateAction, UpdateAction, DeleteAction).
        """
        raise NotImplementedError()

    def create_action_result_element(
        self, instance: dict[str, Any]
    ) -> ActionResultElement | None:
        """
        Create an ActionResponseResultsElement describing the result of this action.
        Defaults to None (to be overridden in subclasses).
        """
        return None

    def write_history(self) -> WriteRequest | None:
        """
        Writes history records directly to the database.
        Returns a WriteRequest with history information for sub-calls, or None.
        """
        information = self.get_full_history_information()

        if self.is_sub_call:
            # For sub-calls, return WriteRequest with information for parent to merge
            write_request = WriteRequest([])
            write_request.information = information
            write_request.user_id = self.user_id
            return write_request

        if not information:
            return None

        # Filter to only include history-tracked models
        information = {
            fqid: info
            for fqid, info in information.items()
            if fqid.split("/")[0] in HISTORY_MODELS
        }

        if not information:
            return None

        # Reserve IDs for history records
        position_id = self.sql.reserve_id("history_position")
        entry_ids = self.sql.reserve_ids("history_entry", len(information))

        # Build touched_fqids from tracking sets
        touched_fqids = self.created_fqids | self.updated_fqids
        if self.user_id and self.user_id > 0:
            touched_fqids.add(fqid_from_collection_and_id("user", self.user_id))

        # Gather meeting_ids for history entries
        collection_to_ids: dict[str, list[int]] = defaultdict(list)
        for fqid in information:
            collection_to_ids[collection_from_fqid(fqid)].append(id_from_fqid(fqid))

        data: dict[str, dict[int, dict[str, Any]]] = {}
        for collection, ids in collection_to_ids.items():
            if model_registry[collection]().try_get_field("meeting_id"):
                data[collection] = self.sql.get_many(collection, ids, ["meeting_id"]) if ids else {}

        # Check which meetings are being deleted
        deleted_meeting_ids = {
            id_from_fqid(fqid)
            for fqid in self.deleted_fqids
            if fqid.startswith("meeting/")
        }

        # Build model_fqid_to_meeting_id mapping
        model_fqid_to_meeting_id = {
            fqid_from_collection_and_id(collection, id_): meeting_id
            for collection, models in data.items()
            for id_, date in models.items()
            if (meeting_id := date.get("meeting_id"))
            and meeting_id not in deleted_meeting_ids
        }

        # Calculate history payloads
        existing_fqids = touched_fqids - self.deleted_fqids
        history_data = calculate_history_event_payloads(
            self.user_id,
            information,
            position_id,
            {m_fqid: e_id for m_fqid, e_id in zip(information, entry_ids)},
            model_fqid_to_meeting_id,
            existing_fqids,
        )

        # Write history records directly to database
        for fqid, fields in history_data:
            collection = collection_from_fqid(fqid)
            id_ = id_from_fqid(fqid)
            self.sql.insert(collection, fields, id_)

        return None

    def get_full_history_information(self) -> HistoryInformation | None:
        """
        Get history information for this action and all cascading ones. Should only be overridden if
        the order should be changed.
        """
        information = self.get_history_information()
        if self.cascaded_actions_history or information:
            if self.own_history_information_first:
                return merge_history_informations(
                    information, self.cascaded_actions_history
                )
            else:
                return merge_history_informations(
                    self.cascaded_actions_history, information
                )
        else:
            return None

    def get_history_information(self) -> HistoryInformation | None:
        """
        Get the history information for this action. Can be overridden to get
        context-dependent information.
        """
        if self.history_information is None:
            return None

        information = {}
        instances = (
            self.get_instances_with_fields(["id", self.history_relation_field])
            if self.history_relation_field
            else self.instances
        )
        for instance in instances:
            fqids = []
            if self.history_relation_field:
                field = self.model.get_field(self.history_relation_field)
                assert isinstance(field, BaseRelationField)
                fqids = transform_to_fqids(
                    instance[self.history_relation_field], field.get_target_collection()
                )
            if not self.history_relation_field or self.add_self_history_information:
                fqids.append(
                    fqid_from_collection_and_id(self.model.collection, instance["id"])
                )
            for fqid in fqids:
                information[fqid] = [self.history_information]
        return information

    def get_instances_with_fields(
        self, fields: list[str], instances: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        if not instances:
            instances = self.instances
        # if any field is missing in any instance, we need to access the database
        if any(not instance.get(field) for field in fields for instance in instances):
            ids = [instance["id"] for instance in instances]
            result = self.sql.get_many(
                self.model.collection,
                ids,
                fields,
            ) if ids else {}
            return list(result.values())
        else:
            return instances

    def get_field_from_instance(self, field: str, instance: dict[str, Any]) -> Any:
        instances = self.get_instances_with_fields([field], [instance])
        return instances[0].get(field) if instances else None


    def validate_fields(self, instance: dict[str, Any]) -> dict[str, Any]:
        """
        Validates and sanitizes all model fields according to the model definition.
        """
        try:
            for field_name in instance:
                if self.model.has_field(field_name):
                    field = self.model.get_field(field_name)
                    instance[field_name] = field.validate(instance[field_name])
        except AssertionError as e:
            raise ActionException(str(e))
        except ValueError as e:
            raise ActionException(str(e))
        return instance

    def validate_relation_fields(self, instance: dict[str, Any]) -> None:
        """
        Validates all relation fields according to the model definition.
        """
        for field in self.model.get_relation_fields():
            if not field.equal_fields or field.own_field_name not in instance:
                continue

            fields = [field.own_field_name]
            for equal_field in field.equal_fields:
                if not (own_equal_field_value := instance.get(equal_field)):
                    db_instance = self.sql.get(
                        self.model.collection,
                        instance["id"],
                        [equal_field],
                    ) or {}
                    if not (own_equal_field_value := db_instance.get(equal_field)):
                        fqid = fqid_from_collection_and_id(
                            self.model.collection, instance["id"]
                        )
                        raise ActionException(
                            f"{fqid} has no value for the field {equal_field}"
                        )
                for instance_field in fields:
                    fqids = transform_to_fqids(
                        instance[instance_field], field.get_target_collection()
                    )
                    if equal_field == "meeting_id":
                        assert_belongs_to_meeting(
                            self.sql, fqids, own_equal_field_value
                        )
                    else:
                        for fqid in fqids:
                            fqid_collection = collection_from_fqid(fqid)
                            fqid_id = id_from_fqid(fqid)
                            related_instance = self.sql.get(
                                fqid_collection,
                                fqid_id,
                                [equal_field],
                            ) or {}
                            if str(related_instance.get(equal_field)) != str(
                                own_equal_field_value
                            ):
                                raise ActionException(
                                    f"The relation {field.own_field_name} requires the following "
                                    f"fields to be equal:\n"
                                    f"{field.own_collection}/{instance['id']}/{equal_field}: "
                                    f"{own_equal_field_value}\n"
                                    f"{fqid}/{equal_field}: "
                                    f"{related_instance.get(equal_field)}"
                                )

    def apply_instance(
        self, instance: dict[str, Any], fqid: FullQualifiedId | None = None
    ) -> None:
        """
        No-op: With direct SQL access, changes are applied directly to the database
        in the respective action classes. This method is kept for compatibility.
        """
        pass

    def execute_other_action(
        self,
        ActionClass: type["Action"],
        action_data: ActionData,
        skip_archived_meeting_check: bool = False,
        skip_history: bool = False,
    ) -> ActionResults | None:
        """
        Executes the given action class as a dependent action with the given action
        data and the given addtional relation models. Merges its own additional
        relation models into it.
        The action is fully executed and tracking sets are merged into this action.
        The attribute skip_archived_meeting_check from the calling class is inherited
        to the called class if set. Usually this is needed for cascading deletes from
        outside of meeting.
        """
        with make_span(self.env, f"other action {ActionClass}"):
            if self.skip_archived_meeting_check:
                skip_archived_meeting_check = self.skip_archived_meeting_check

            action = ActionClass(
                self.services,
                self.datastore,
                self.relation_manager,
                self.logging,
                self.env,
                skip_archived_meeting_check,
                sql=self.sql,  # Pass the same SqlHelper instance for transaction consistency
            )
            write_request, action_results = action.perform(
                action_data, self.user_id, internal=True, is_sub_call=True
            )

            # Merge tracking sets from sub-action
            self.created_fqids.update(action.created_fqids)
            self.updated_fqids.update(action.updated_fqids)
            self.deleted_fqids.update(action.deleted_fqids)

            # Merge history information
            if write_request and not skip_history and write_request.information:
                merge_history_informations(
                    self.cascaded_actions_history, write_request.information
                )
            return action_results

    def get_on_success(self, action_data: ActionData) -> Callable[[], None] | None:
        """
        Can be overridden by actions to return a cleanup method to execute
        after the result was successfully written to the DS.
        """
        return None

    def get_on_failure(self, action_data: ActionData) -> Callable[[], None] | None:
        """
        Can be overridden by actions to return a cleanup method to execute
        after an error appeared in an action.
        """
        return None

    def execute_presenter(
        self, PresenterClass: type[BasePresenter], payload: Any
    ) -> Any:
        presenter_instance = PresenterClass(
            payload,
            self.services,
            self.datastore,
            self.logging,
            self.user_id,
        )
        presenter_instance.validate()
        return presenter_instance.get_result()


def merge_history_informations(
    a: HistoryInformation | None, *other: HistoryInformation | None
) -> HistoryInformation:
    """
    Merges multiple history informations. All latter ones are merged into the first one.
    """
    if a is None:
        a = {}
    for b in other:
        if b is None:
            b = {}
        for fqid, information in b.items():
            if fqid in a:
                a[fqid].extend(information)
            else:
                a[fqid] = information
    return a
