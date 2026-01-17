# SQL Migration Guide

This document describes the migration from the event-based datastore abstraction to direct SQL access.

## Overview

The migration introduces a new `SqlHelper` class that provides direct SQL operations for reading and writing data. The key benefits are:

1. **Simplified Architecture**: No more event system for writes
2. **Transaction Consistency**: PostgreSQL transactions (REPEATABLE READ) ensure data consistency
3. **Performance**: Direct SQL is more efficient than event-based batching
4. **Maintainability**: Clearer code with direct database operations

## Current State

### Completed

1. **SqlHelper Class** (`services/database/sql_helper.py`)
   - Read operations: `get`, `get_many`, `filter`, `exists`, `count`, `min`, `max`, `get_all`
   - Write operations: `insert`, `update`, `delete`, `add_to_list`, `remove_from_list`
   - ID management: `reserve_id`, `reserve_ids`
   - Intermediate table handling for N:M relations

2. **Base Action Class** (`action/action.py`)
   - Added `self.sql: SqlHelper` attribute
   - SqlHelper is passed to nested actions via `execute_other_action`

3. **ActionHandler** (`action/action_handler.py`)
   - Creates SqlHelper instance alongside datastore
   - Passes SqlHelper to all actions

4. **Generic Actions**
   - `CreateAction`: Uses `self.sql.insert()` for direct writes
   - `UpdateAction`: Uses `self.sql.update()` for direct writes
   - `DeleteAction`: Uses `self.sql.delete()` for direct writes
   - All generic actions still generate events for history tracking

5. **RelationManager** (`action/relations/relation_manager.py`)
   - Updated to write relation updates directly via `self.sql.update()`

6. **Migrated Mixins**
   - `WeightMixin`: Uses `self.sql.max()`
   - `LinearSortMixin`: Uses `self.sql.filter()`
   - `ImportMixins`: Uses `self.sql.reserve_id()`

### Pending

- 149 action files still use `self.datastore` for reads
- These can continue working with the hybrid approach
- Gradual migration can be done over time

## Migration Patterns

### Read Operations

**Old Pattern (using datastore):**
```python
from ...shared.patterns import fqid_from_collection_and_id

meeting = self.datastore.get(
    fqid_from_collection_and_id("meeting", instance["meeting_id"]),
    ["name", "is_active"],
    lock_result=False,
)
```

**New Pattern (using sql):**
```python
meeting = self.sql.get(
    "meeting",
    instance["meeting_id"],
    ["name", "is_active"],
    lock_result=False,
)
```

### Get Many Operations

**Old Pattern:**
```python
from ...services.database.commands import GetManyRequest

result = self.datastore.get_many([
    GetManyRequest("meeting", meeting_ids, ["name", "committee_id"]),
    GetManyRequest("user", user_ids, ["first_name", "last_name"]),
], lock_result=False)
meetings = result["meeting"]
users = result["user"]
```

**New Pattern:**
```python
meetings = self.sql.get_many("meeting", meeting_ids, ["name", "committee_id"], lock_result=False)
users = self.sql.get_many("user", user_ids, ["first_name", "last_name"], lock_result=False)
```

### Filter Operations

**Old Pattern:**
```python
from ...shared.filters import FilterOperator

results = self.datastore.filter(
    "motion",
    FilterOperator("meeting_id", "=", meeting_id),
    ["id", "title"],
    lock_result=False,
)
```

**New Pattern:**
```python
from ...shared.filters import FilterOperator

results = self.sql.filter(
    "motion",
    FilterOperator("meeting_id", "=", meeting_id),
    ["id", "title"],
    lock_result=False,
)
```

### Aggregate Operations

**Old Pattern:**
```python
max_weight = self.datastore.max("agenda_item", filter_, "weight")
count = self.datastore.count("motion", filter_)
exists = self.datastore.exists("user", filter_)
```

**New Pattern:**
```python
max_weight = self.sql.max("agenda_item", filter_, "weight")
count = self.sql.count("motion", filter_)
exists = self.sql.exists("user", filter_)
```

### ID Reservation

**Old Pattern:**
```python
new_id = self.datastore.reserve_id("meeting")
new_ids = self.datastore.reserve_ids("motion", 5)
```

**New Pattern:**
```python
new_id = self.sql.reserve_id("meeting")
new_ids = self.sql.reserve_ids("motion", 5)
```

## Transaction Model

The new architecture uses a single PostgreSQL transaction per request:

1. `ActionHandler` starts a transaction when a request comes in
2. All actions in the request share the same transaction
3. `execute_other_action` passes the same `SqlHelper` instance
4. All writes are immediately visible to subsequent reads in the same transaction
5. On success: `connection.commit()` - all changes are persisted
6. On error: `connection.rollback()` - all changes are discarded

## Backward Compatibility

The migration maintains backward compatibility:

1. **`self.datastore` still works**: Actions can continue using `self.datastore` for reads
2. **Events still generated**: Generic actions still generate events for history tracking
3. **Hybrid writes**: The system writes via both direct SQL and events (events will be removed later)

## How to Migrate an Action File

1. Replace `self.datastore.get(fqid, fields)` with `self.sql.get(collection, id, fields)`
2. Replace `self.datastore.get_many([...])` with multiple `self.sql.get_many()` calls
3. Replace `self.datastore.filter(...)` with `self.sql.filter(...)`
4. Replace `self.datastore.exists/count/min/max(...)` with `self.sql.exists/count/min/max(...)`
5. Replace `self.datastore.reserve_id(s)(...)` with `self.sql.reserve_id(s)(...)`
6. Run tests to verify the migration

## Files Modified

### Core Infrastructure
- `openslides_backend/services/database/sql_helper.py` (new)
- `openslides_backend/action/action.py`
- `openslides_backend/action/action_handler.py`
- `openslides_backend/action/generics/create.py`
- `openslides_backend/action/generics/update.py`
- `openslides_backend/action/generics/delete.py`
- `openslides_backend/action/relations/relation_manager.py`

### Migrated Mixins
- `openslides_backend/action/mixins/weight_mixin.py`
- `openslides_backend/action/mixins/import_mixins.py`
- `openslides_backend/action/mixins/linear_sort_mixin.py`

### Migrated Action Files
- `openslides_backend/action/actions/meeting/update.py` (7 get calls)
- `openslides_backend/action/actions/speaker/create.py` (15+ calls: get, get_many, filter, min, max, exists)
- `openslides_backend/action/actions/poll/stop.py` (1 get call)
- `openslides_backend/action/actions/motion/set_state.py` (4 get calls)
- `openslides_backend/action/actions/motion/update.py` (5 get, 1 get_many)
- `openslides_backend/action/actions/committee/update.py` (3 get_many, 1 get)
- `openslides_backend/action/actions/user/save_saml_account.py` (1 get, 5 filter)
- `openslides_backend/action/actions/poll/mixins.py` (4 get_many, 2 get)
- `openslides_backend/action/actions/motion/base_create_forwarded.py` (13 get, 8 get_many, 2 filter)
- `openslides_backend/action/actions/agenda_item/forward.py` (1 get, 11 get_many, 1 max, 1 reserve_ids)
- `openslides_backend/action/actions/meeting_user/history_mixin.py` (7 get, 5 get_many)
- `openslides_backend/action/actions/mediafile/mixins.py` (7 get, 3 filter, 1 get_many)
- `openslides_backend/action/actions/motion/set_number_mixin.py` (6 get, 1 max, 1 exists)
- `openslides_backend/action/actions/user/merge_together.py` (5 get_many, 4 filter)
- `openslides_backend/action/actions/meeting/import_.py` (1 filter, 1 get_all, 5 reserve_ids, 2 get, 1 get_many)
- `openslides_backend/action/actions/meeting_user/mixin.py` (2 filter, 3 get_many, 4 get, 1 exists)
- `openslides_backend/action/actions/user/user_mixins.py` (8 calls)
- `openslides_backend/action/actions/mediafile/move.py` (8 calls)
- `openslides_backend/action/actions/group/delete.py` (7 get, 1 filter)
- `openslides_backend/action/actions/motion_comment/create_delete_update.py` (4 get, 1 filter, 1 exists)
- `openslides_backend/action/actions/meeting/clone.py` (4 get)
- `openslides_backend/action/actions/user/participant_common.py` (3 get, 1 filter)
- `openslides_backend/action/actions/motion/payload_validation_mixin.py` (4 get)
- `openslides_backend/action/actions/agenda_item/assign.py` (4 get, 1 filter)
- `openslides_backend/action/actions/speaker/update.py` (5 get)
- `openslides_backend/action/actions/poll/create.py` (3 get)
- `openslides_backend/action/actions/meeting/archive.py` (1 get, 2 exists)
- `openslides_backend/action/actions/motion/reset_state.py` (3 get)
- `openslides_backend/action/actions/motion/set_recommendation.py` (3 get)
- `openslides_backend/action/actions/speaker/speak.py` (2 get, 1 filter)
- `openslides_backend/action/actions/speaker/delete.py` (3 get)
- `openslides_backend/action/actions/speaker/end_speech.py` (2 get, 1 filter)

## Remaining Work

~108 action files still need migration. Use the following command to find remaining files:
```bash
grep -r "self.datastore\.\(get\|filter\|exists\|count\|min\|max\|reserve\|get_many\|get_all\)" --include="*.py" openslides_backend/action/actions/ | grep -v "__pycache__" | cut -d: -f1 | sort | uniq -c | sort -rn
```
