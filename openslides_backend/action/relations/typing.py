from typing import Any, TypedDict, Union

from ...shared.patterns import FullQualifiedField, Identifier, IdentifierList

# Type alias for a partial model dictionary
PartialModel = dict[str, Any]


class FieldUpdateElement(TypedDict):
    type: str
    value: Identifier | IdentifierList | None
    modified_element: Identifier


class ListUpdateElement(TypedDict):
    type: str
    add: IdentifierList
    remove: IdentifierList


RelationUpdateElement = Union[FieldUpdateElement, ListUpdateElement]
RelationFieldUpdates = dict[FullQualifiedField, FieldUpdateElement]
RelationUpdates = dict[FullQualifiedField, RelationUpdateElement]
