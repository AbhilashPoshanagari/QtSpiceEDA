"""S2 spike: QUndoCommand wrappers around the Qt-free commands (scratch only).

``UndoCommand`` wraps one core command 1:1 (rule 22): redo -> apply, undo -> revert,
id -> merge_id, mergeWith -> merge. A merged drag that ends where it started is made
obsolete, so QUndoStack drops it.

``push_macro`` is the alternative to one compound command: the children of a
CompoundCommand pushed one by one between beginMacro/endMacro.
"""

from __future__ import annotations

from commands import Command, CompoundCommand, MoveGlyph, SnapshotCommand
from PySide6.QtGui import QUndoCommand, QUndoStack
from state import DesignState


class UndoCommand(QUndoCommand):
    def __init__(self, state: DesignState, command: Command) -> None:
        super().__init__(command.description)
        self.state = state
        self.command = command

    def redo(self) -> None:
        self.command.apply(self.state)

    def undo(self) -> None:
        self.command.revert(self.state)

    def id(self) -> int:
        return self.command.merge_id

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 (Qt name)
        if not isinstance(other, UndoCommand) or not self.command.merge(other.command):
            return False
        inner = self.command
        if isinstance(inner, SnapshotCommand):
            inner = inner.inner
        if isinstance(inner, MoveGlyph) and inner.is_noop():
            self.setObsolete(True)
        return True


def push_macro(stack: QUndoStack, state: DesignState, compound: CompoundCommand) -> None:
    stack.beginMacro(compound.description)
    for child in compound.children:
        stack.push(UndoCommand(state, child))
    stack.endMacro()
