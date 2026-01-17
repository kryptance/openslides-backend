"""
Direct SQL access helper class.

This module provides the SqlHelper class for direct SQL operations on the database.
It replaces the event-based system with direct INSERT/UPDATE/DELETE operations,
relying on PostgreSQL transactions (REPEATABLE READ isolation) for consistency.
"""

from typing import Any

from psycopg import Connection, rows, sql
from psycopg.errors import (
    CheckViolation,
    DatatypeMismatch,
    GeneratedAlways,
    InFailedSqlTransaction,
    NotNullViolation,
    UndefinedColumn,
    UndefinedTable,
    UniqueViolation,
)

from openslides_backend.models.base import model_registry
from openslides_backend.models.fields import (
    Field,
    GenericRelationListField,
    RelationListField,
)
from openslides_backend.services.postgresql.db_connection_handling import (
    retry_on_db_failure,
)
from openslides_backend.shared.exceptions import (
    BadCodingException,
    DatabaseException,
    InvalidFormat,
    ModelDoesNotExist,
    ModelExists,
    RelationException,
)
from openslides_backend.shared.filters import Filter
from openslides_backend.shared.otel import make_span
from openslides_backend.shared.patterns import (
    Collection,
    Id,
    fqid_from_collection_and_id,
)
from openslides_backend.shared.typing import PartialModel

from ...shared.interfaces.env import Env
from ...shared.interfaces.logging import LoggingModule
from .mapped_fields import MappedFields
from .query_helper import SqlQueryHelper


class SqlHelper(SqlQueryHelper):
    """
    Direct SQL access helper - transaction handles consistency.

    This class provides direct SQL operations for reading and writing data.
    It uses PostgreSQL transactions with REPEATABLE READ isolation to ensure
    consistency. All reads see a consistent snapshot within the transaction,
    and writes are immediately visible to subsequent reads in the same transaction.

    Transaction scope:
    - One transaction per request - ActionHandler starts the transaction
    - All actions share the same transaction (when atomic=True)
    - Nested actions (execute_other_action) run in the same transaction
    - On error: connection.rollback() - all changes are rolled back
    - On success: connection.commit() - all changes are committed
    """

    def __init__(
        self, connection: Connection[rows.DictRow], logging: LoggingModule, env: Env
    ) -> None:
        self.env = env
        self.logger = logging.getLogger(__name__)
        self.connection = connection

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get(
        self,
        collection: str,
        id_: int,
        fields: list[str] | None = None,
        lock_result: bool = True,
    ) -> PartialModel | None:
        """
        Get a single model by ID.

        Args:
            collection: The collection name (e.g., "meeting", "user")
            id_: The model ID
            fields: List of fields to retrieve. If None, retrieves all fields.
            lock_result: Whether to lock the row for update (default True)

        Returns:
            The model as a dict, or None if not found
        """
        if id_ <= 0:
            raise InvalidFormat("Id must be positive.")

        mapped_fields = MappedFields(fields) if fields else MappedFields()
        if fields and "id" not in mapped_fields.unique_fields:
            mapped_fields.unique_fields.append("id")

        columns = self.build_select_from_mapped_fields(mapped_fields)
        query = sql.SQL("SELECT {columns} FROM {view} WHERE id = %s").format(
            columns=columns,
            view=sql.Identifier(collection),
        )
        if lock_result:
            query += sql.SQL(" FOR UPDATE")

        result = self._execute_read_query(collection, query, (id_,), mapped_fields)
        return result[0] if result else None

    def get_many(
        self,
        collection: str,
        ids: list[int],
        fields: list[str] | None = None,
        lock_result: bool = True,
    ) -> dict[int, PartialModel]:
        """
        Get multiple models by their IDs.

        Args:
            collection: The collection name
            ids: List of model IDs
            fields: List of fields to retrieve. If None, retrieves all fields.
            lock_result: Whether to lock the rows for update

        Returns:
            Dict mapping ID to model
        """
        if not ids:
            return {}

        for id_ in ids:
            if id_ <= 0:
                raise InvalidFormat("Id must be positive.")

        mapped_fields = MappedFields(fields) if fields else MappedFields()
        if fields and "id" not in mapped_fields.unique_fields:
            mapped_fields.unique_fields.append("id")

        columns = self.build_select_from_mapped_fields(mapped_fields)
        query = sql.SQL("SELECT {columns} FROM {view} WHERE id = ANY(%s)").format(
            columns=columns,
            view=sql.Identifier(collection),
        )
        if lock_result:
            query += sql.SQL(" FOR UPDATE")

        results = self._execute_read_query(collection, query, (ids,), mapped_fields)
        return {row["id"]: row for row in results}

    def filter(
        self,
        collection: str,
        filter_: Filter,
        fields: list[str] | None = None,
        lock_result: bool = True,
    ) -> dict[int, PartialModel]:
        """
        Filter models by criteria.

        Args:
            collection: The collection name
            filter_: Filter object defining the criteria
            fields: List of fields to retrieve. If None, retrieves all fields.
            lock_result: Whether to lock the rows for update

        Returns:
            Dict mapping ID to model for all matching models
        """
        mapped_fields = MappedFields(fields) if fields else MappedFields()
        if fields and "id" not in mapped_fields.unique_fields:
            mapped_fields.unique_fields.append("id")

        query, arguments = self.build_filter_query(
            collection, filter_, mapped_fields, None
        )
        if lock_result:
            query += sql.SQL(" FOR UPDATE")

        results = self._execute_read_query(
            collection, query, tuple(arguments), mapped_fields
        )
        return {row["id"]: row for row in results}

    def exists(
        self,
        collection: str,
        filter_: Filter,
        lock_result: bool = True,
    ) -> bool:
        """
        Check if any models match the filter.

        Args:
            collection: The collection name
            filter_: Filter object defining the criteria
            lock_result: Whether to lock the rows for update

        Returns:
            True if at least one model matches, False otherwise
        """
        return self.count(collection, filter_, lock_result) > 0

    def count(
        self,
        collection: str,
        filter_: Filter | None,
        lock_result: bool = True,
    ) -> int:
        """
        Count models matching the filter.

        Args:
            collection: The collection name
            filter_: Filter object defining the criteria (or None for all)
            lock_result: Whether to lock the rows for update

        Returns:
            Number of matching models
        """
        return self._aggregate("count", collection, filter_, "*", lock_result) or 0

    def min(
        self,
        collection: str,
        filter_: Filter | None,
        field: str,
        lock_result: bool = True,
    ) -> int | None:
        """
        Get minimum value of a field for models matching the filter.

        Args:
            collection: The collection name
            filter_: Filter object defining the criteria (or None for all)
            field: Field name to get minimum of
            lock_result: Whether to lock the rows for update

        Returns:
            Minimum value or None if no models match
        """
        return self._aggregate("min", collection, filter_, field, lock_result)

    def max(
        self,
        collection: str,
        filter_: Filter | None,
        field: str,
        lock_result: bool = True,
    ) -> int | None:
        """
        Get maximum value of a field for models matching the filter.

        Args:
            collection: The collection name
            filter_: Filter object defining the criteria (or None for all)
            field: Field name to get maximum of
            lock_result: Whether to lock the rows for update

        Returns:
            Maximum value or None if no models match
        """
        return self._aggregate("max", collection, filter_, field, lock_result)

    def get_all(
        self,
        collection: str,
        fields: list[str] | None = None,
        lock_result: bool = True,
    ) -> dict[int, PartialModel]:
        """
        Get all models in a collection.

        Args:
            collection: The collection name
            fields: List of fields to retrieve. If None, retrieves all fields.
            lock_result: Whether to lock the rows for update

        Returns:
            Dict mapping ID to model for all models in the collection
        """
        mapped_fields = MappedFields(fields) if fields else MappedFields()
        if fields and "id" not in mapped_fields.unique_fields:
            mapped_fields.unique_fields.append("id")

        columns = self.build_select_from_mapped_fields(mapped_fields)
        query = sql.SQL("SELECT {columns} FROM {view}").format(
            columns=columns,
            view=sql.Identifier(collection),
        )
        if lock_result:
            query += sql.SQL(" FOR UPDATE")

        results = self._execute_read_query(collection, query, (), mapped_fields)
        return {row["id"]: row for row in results}

    def _aggregate(
        self,
        method: str,
        collection: str,
        filter_: Filter | None,
        field_or_star: str,
        lock_result: bool,
    ) -> int | None:
        """
        Execute an aggregate function (count, min, max).
        """
        aggregate_function = sql.SQL("{aggregate_function}({agg_field})").format(
            agg_field=(
                sql.Identifier(field_or_star)
                if field_or_star != "*"
                else sql.SQL("*")
            ),
            aggregate_function=sql.SQL(method),
        )
        query, arguments = self.build_filter_query(
            collection, filter_, None, aggregate_function
        )

        try:
            with self.connection.cursor() as curs:
                results = curs.execute(query, tuple(arguments)).fetchall()
                if results:
                    return results[0].get(method)
                return None
        except UndefinedColumn as e:
            column = e.args[0].split('"')[1]
            raise InvalidFormat(
                f"Field '{column}' does not exist in collection '{collection}': {e}"
            )
        except UndefinedTable as e:
            raise InvalidFormat(
                f"Collection '{collection}' does not exist in the database: {e}"
            )
        except Exception as e:
            raise DatabaseException(f"Unexpected error reading from database: {e}")

    def _execute_read_query(
        self,
        collection: str,
        query: sql.Composed,
        arguments: tuple,
        mapped_fields: MappedFields,
    ) -> list[PartialModel]:
        """
        Execute a read query and return the results.
        """
        try:
            with self.connection.cursor() as curs:
                results = curs.execute(query, arguments).fetchall()
                # Filter out None values unless we need the whole model
                if not mapped_fields.needs_whole_model and mapped_fields.unique_fields:
                    return [
                        {k: v for k, v in row.items() if v is not None}
                        for row in results
                    ]
                return list(results)
        except UndefinedColumn as e:
            column = e.args[0].split('"')[1]
            raise InvalidFormat(
                f"Field '{column}' does not exist in collection '{collection}': {e}"
            )
        except UndefinedTable as e:
            raise InvalidFormat(
                f"Collection '{collection}' does not exist in the database: {e}"
            )
        except Exception as e:
            raise DatabaseException(f"Unexpected error reading from database: {e}")

    # =========================================================================
    # WRITE OPERATIONS
    # =========================================================================

    def insert(
        self,
        collection: str,
        fields: dict[str, Any],
        id_: int | None = None,
    ) -> int:
        """
        Insert a new model.

        Args:
            collection: The collection name
            fields: Dict of field names to values
            id_: Optional ID (if not provided, uses sequence)

        Returns:
            The ID of the inserted model
        """
        with make_span(self.env, f"sql insert {collection}"):
            simple_fields, intermediate_tables = self._get_simple_and_intermediate(
                fields, collection
            )
            if id_ and "id" not in simple_fields:
                simple_fields["id"] = id_

            if not simple_fields:
                # Insert with only default values
                statement = sql.SQL(
                    "INSERT INTO {table_name} DEFAULT VALUES RETURNING id"
                ).format(table_name=sql.Identifier(f"{collection}_t"))
                id_ = self._execute_write(statement, [], collection, id_)
            else:
                statement = sql.SQL(
                    "INSERT INTO {table_name} ({columns}) VALUES ({values}) RETURNING id"
                ).format(
                    table_name=sql.Identifier(f"{collection}_t"),
                    columns=sql.SQL(", ").join(map(sql.Identifier, simple_fields)),
                    values=sql.SQL(", ").join(
                        sql.SQL("%s") for _ in range(len(simple_fields))
                    ),
                )
                id_ = self._execute_write(
                    statement, list(simple_fields.values()), collection, id_
                )

            # Handle N:M relations
            self._write_to_intermediate_tables(fields, intermediate_tables, id_, collection)
            return id_

    def update(
        self,
        collection: str,
        id_: int,
        fields: dict[str, Any],
    ) -> None:
        """
        Update an existing model.

        Args:
            collection: The collection name
            id_: The model ID
            fields: Dict of field names to new values
        """
        if not fields:
            return

        with make_span(self.env, f"sql update {collection}/{id_}"):
            simple_fields, intermediate_tables = self._get_simple_and_intermediate(
                fields, collection
            )

            # Handle intermediate table updates
            self._delete_from_intermediate_tables(
                fields, intermediate_tables, id_, collection, directly=False
            )
            self._write_to_intermediate_tables(fields, intermediate_tables, id_, collection)

            if simple_fields:
                statement = sql.SQL(
                    "UPDATE {table_name} SET {assignments} WHERE id = %s"
                ).format(
                    table_name=sql.Identifier(f"{collection}_t"),
                    assignments=sql.SQL(", ").join(
                        sql.SQL("{field} = %s").format(field=sql.Identifier(field_name))
                        for field_name in simple_fields
                    ),
                )
                arguments = list(simple_fields.values()) + [id_]
                self._execute_write(statement, arguments, collection, id_, return_id=False)

    def delete(
        self,
        collection: str,
        id_: int,
    ) -> None:
        """
        Delete a model.

        Args:
            collection: The collection name
            id_: The model ID
        """
        with make_span(self.env, f"sql delete {collection}/{id_}"):
            statement = sql.SQL("DELETE FROM {table_name} WHERE id = %s").format(
                table_name=sql.Identifier(f"{collection}_t")
            )
            self._execute_write(statement, [id_], collection, id_, return_id=False)

    def add_to_list(
        self,
        collection: str,
        id_: int,
        field: str,
        values: list,
    ) -> None:
        """
        Add values to a list field.

        For N:M relations (intermediate tables), this inserts rows.
        For array fields, this appends to the array.

        Args:
            collection: The collection name
            id_: The model ID
            field: The list field name
            values: Values to add
        """
        if not values:
            return

        collection_cls = model_registry[collection]()
        field_obj = collection_cls.get_field(field)

        if self._is_primary_nm_relation(field_obj):
            # N:M relation - insert into intermediate table
            self._write_to_intermediate_tables({field: values}, {field: field_obj}, id_, collection)
        else:
            # Array field - use array concatenation
            list_type = type(values[0])
            statement = sql.SQL(
                """
                UPDATE {table_name}
                SET {field} = ARRAY(
                    SELECT DISTINCT unnest(COALESCE({field}, ARRAY[]::integer[]) || %s{type})
                )
                WHERE id = %s
                """
            ).format(
                table_name=sql.Identifier(f"{collection}_t"),
                field=sql.Identifier(field),
                type=self.get_array_type(list_type),
            )
            self._execute_write(statement, [values, id_], collection, id_, return_id=False)

    def remove_from_list(
        self,
        collection: str,
        id_: int,
        field: str,
        values: list,
    ) -> None:
        """
        Remove values from a list field.

        For N:M relations (intermediate tables), this deletes rows.
        For array fields, this removes from the array.

        Args:
            collection: The collection name
            id_: The model ID
            field: The list field name
            values: Values to remove
        """
        if not values:
            return

        collection_cls = model_registry[collection]()
        field_obj = collection_cls.get_field(field)

        if self._is_primary_nm_relation(field_obj):
            # N:M relation - delete from intermediate table
            self._delete_from_intermediate_tables(
                {field: values}, {field: field_obj}, id_, collection, directly=True
            )
        else:
            # Array field - use array removal
            list_type = type(values[0])
            statement = sql.SQL(
                """
                UPDATE {table_name}
                SET {field} = ARRAY(
                    SELECT unnest({field})
                    EXCEPT
                    SELECT unnest(%s{type})
                )
                WHERE id = %s
                """
            ).format(
                table_name=sql.Identifier(f"{collection}_t"),
                field=sql.Identifier(field),
                type=self.get_array_type(list_type),
            )
            self._execute_write(statement, [values, id_], collection, id_, return_id=False)

    # =========================================================================
    # ID MANAGEMENT
    # =========================================================================

    @retry_on_db_failure
    def reserve_id(self, collection: str) -> int:
        """
        Reserve a single ID for a collection.

        Args:
            collection: The collection name

        Returns:
            The reserved ID
        """
        return self.reserve_ids(collection, 1)[0]

    @retry_on_db_failure
    def reserve_ids(self, collection: str, amount: int) -> list[int]:
        """
        Reserve multiple IDs for a collection.

        Args:
            collection: The collection name
            amount: Number of IDs to reserve

        Returns:
            List of reserved IDs
        """
        with make_span(self.env, f"reserve ids {collection}"):
            if amount <= 0:
                raise InvalidFormat(f"Amount must be >= 1, not {amount}.")

            statement = sql.SQL(
                "SELECT nextval('{collection}_t_id_seq') FROM generate_series(1, {amount})"
            ).format(
                collection=sql.SQL(collection),
                amount=sql.Literal(amount),
            )
            with self.connection.cursor() as curs:
                result = curs.execute(statement).fetchall()
                if not result:
                    raise BadCodingException("db id sequence broken.")
                ids = [item.get("nextval", 0) for item in result]
                self.logger.info(f"{len(ids)} ids reserved for {collection}")
                return ids

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _is_primary_nm_relation(self, field: Field) -> bool:
        """Check if a field is a primary N:M relation with an intermediate table."""
        return bool(
            field.is_primary
            and field.write_fields
            and isinstance(field, (RelationListField, GenericRelationListField))
        )

    def _get_simple_and_intermediate(
        self, fields: dict[str, Any], collection: str
    ) -> tuple[dict[str, Any], dict[str, Field]]:
        """
        Split fields into simple fields and intermediate table fields.

        Returns:
            Tuple of (simple_fields dict, intermediate_tables dict)
        """
        collection_cls = model_registry[collection]()
        simple_fields = {}
        intermediate_tables = {}

        for field_name, value in fields.items():
            if field_name.startswith("meta_"):
                continue
            field = collection_cls.get_field(field_name)
            if field.is_view_field or field_name == "organization_id":
                continue
            if self._is_primary_nm_relation(field):
                intermediate_tables[field_name] = field
            else:
                simple_fields[field_name] = value

        return simple_fields, intermediate_tables

    def _write_to_intermediate_tables(
        self,
        fields: dict[str, Any],
        intermediate_tables: dict[str, Field],
        id_: int,
        collection: str,
    ) -> None:
        """Write rows to intermediate tables for N:M relations."""
        for field_name, field in intermediate_tables.items():
            if not field.write_fields:
                raise BadCodingException(
                    f"The field {field_name} should be in an n:m relation."
                )
            intermediate_table, close_side, far_side, _ = field.write_fields
            values = fields.get(field_name)
            if not values:
                continue

            statement = sql.SQL(
                """
                INSERT INTO {table_name} ({columns})
                VALUES {placeholders}
                ON CONFLICT ({columns}) DO NOTHING
                """
            ).format(
                table_name=sql.Identifier(intermediate_table),
                columns=sql.Identifier(close_side)
                + sql.SQL(", ")
                + sql.Identifier(far_side),
                placeholders=sql.SQL(", ").join(
                    sql.SQL(f"(%(own_id)s, %({nr})s)") for nr in range(len(values))
                ),
            )
            arguments = {
                **{str(i): val for i, val in enumerate(values)},
                "own_id": id_,
            }
            self._execute_write(
                statement, arguments, collection, id_, return_id=False
            )

    def _delete_from_intermediate_tables(
        self,
        fields: dict[str, Any],
        intermediate_tables: dict[str, Field],
        id_: int,
        collection: str,
        directly: bool,
    ) -> None:
        """
        Delete from intermediate tables.

        If directly=True, deletes rows matching the values.
        If directly=False, deletes rows NOT matching the values (for replacement).
        """
        for field_name, field in intermediate_tables.items():
            if not field.write_fields:
                raise BadCodingException(
                    f"The field {field_name} should be in an n:m relation."
                )
            other_column_values = fields.get(field_name)
            if directly and not other_column_values:
                continue
            if not other_column_values:
                other_column_values = []
            if not isinstance(other_column_values, list):
                other_column_values = [other_column_values]

            intermediate_table, own_column, other_column, *_ = field.write_fields
            statement = sql.SQL(
                """
                DELETE FROM {table_name}
                WHERE ({own_column} = %s AND {negation}({other_column} = ANY(%s)))
                """
            ).format(
                table_name=sql.Identifier(intermediate_table),
                own_column=sql.Identifier(own_column),
                other_column=sql.Identifier(other_column),
                negation=sql.SQL("") if directly else sql.SQL("NOT "),
            )
            self._execute_write(
                statement, [id_, other_column_values], collection, id_, return_id=False
            )

    def _execute_write(
        self,
        statement: sql.Composed,
        arguments: list[Any] | dict[str, Any],
        collection: str,
        target_id: int | None,
        return_id: bool = True,
    ) -> int:
        """
        Execute a write statement.

        Returns the ID if return_id=True, otherwise returns 0.
        """
        error_fqid = fqid_from_collection_and_id(collection, target_id or 0)
        try:
            with self.connection.cursor() as curs:
                curs.execute(statement, arguments)
                if return_id:
                    result = curs.fetchone()
                    if not result:
                        raise ModelDoesNotExist(error_fqid)
                    return result.get("id", 0)
                return 0
        except InFailedSqlTransaction as e:
            raise BadCodingException(
                f"Tried to write {error_fqid} in a broken transaction: {e}"
            )
        except UniqueViolation as e:
            if "duplicate key value violates unique constraint" in e.args[0]:
                if "Key (id)" in e.args[0]:
                    raise ModelExists(error_fqid)
                else:
                    raise RelationException(
                        f"Relation from {error_fqid} violates UNIQUE constraint: {e}"
                    )
            raise
        except NotNullViolation as e:
            column = e.args[0].split('"')[1]
            raise BadCodingException(
                f"Missing required field '{column}' in '{error_fqid}': {e}"
            )
        except GeneratedAlways as e:
            raise BadCodingException(
                f"Used a field that must only be generated by the database: {e}"
            )
        except UndefinedColumn as e:
            column = e.args[0].split('"')[1]
            raise InvalidFormat(
                f"Field '{column}' does not exist in collection '{collection}': {e}"
            )
        except UndefinedTable as e:
            table = e.args[0].split('"')[1]
            if table.startswith(("gm_", "nm_")):
                raise InvalidFormat(
                    f"Intermediate table '{table}' does not exist: {e}"
                )
            else:
                raise InvalidFormat(
                    f"Collection '{collection}' does not exist in the database: {e}"
                )
        except DatatypeMismatch as e:
            column = e.args[0].split('"')[1]
            raise InvalidFormat(
                f"Invalid data type for '{column}' in {error_fqid}. {e}"
            )
        except CheckViolation as e:
            raise InvalidFormat(
                f"Check constraint violation for {error_fqid}: {e}"
            )

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def truncate_db(self) -> None:
        """Truncate all tables in the database."""
        with self.connection.cursor() as curs:
            curs.execute("SELECT tablename from pg_tables WHERE schemaname = 'public'")
            table_names = ", ".join(table["tablename"] for table in curs.fetchall())
        self.connection.execute(f"TRUNCATE TABLE {table_names} RESTART IDENTITY;")
