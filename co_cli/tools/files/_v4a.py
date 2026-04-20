"""V4A multi-file patch parser — ported from hermes-agent/tools/patch_parser.py.

V4A format:
    *** Begin Patch
    *** Update File: path/to/file.py
    @@ optional context hint @@
     context line
    -removed line
    +added line
    *** Add File: path/to/new.py
    +file content
    *** Delete File: path/to/old.py
    *** End Patch
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class HunkLine:
    prefix: str  # ' ', '-', or '+'
    content: str


@dataclass
class Hunk:
    context_hint: str | None = None
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    operation: OperationType
    file_path: str
    hunks: list[Hunk] = field(default_factory=list)


def parse_v4a_patch(patch_content: str) -> tuple[list[PatchOperation], str | None]:  # noqa: C901 — finite-state parser; branches reflect V4A grammar, not tangled logic
    """Parse a V4A format patch. Returns (operations, error_message)."""
    lines = patch_content.split("\n")
    operations: list[PatchOperation] = []

    start_idx = None
    end_idx = None
    for idx, line in enumerate(lines):
        if "*** Begin Patch" in line or "***Begin Patch" in line:
            start_idx = idx
        elif "*** End Patch" in line or "***End Patch" in line:
            end_idx = idx
            break

    if start_idx is None:
        start_idx = -1
    if end_idx is None:
        end_idx = len(lines)

    i = start_idx + 1
    current_op: PatchOperation | None = None
    current_hunk: Hunk | None = None

    while i < end_idx:
        line = lines[i]

        update_match = re.match(r"\*\*\*\s*Update\s+File:\s*(.+)", line)
        add_match = re.match(r"\*\*\*\s*Add\s+File:\s*(.+)", line)
        delete_match = re.match(r"\*\*\*\s*Delete\s+File:\s*(.+)", line)

        if update_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            current_op = PatchOperation(OperationType.UPDATE, update_match.group(1).strip())
            current_hunk = None
        elif add_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            current_op = PatchOperation(OperationType.ADD, add_match.group(1).strip())
            current_hunk = Hunk()
        elif delete_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            current_op = PatchOperation(OperationType.DELETE, delete_match.group(1).strip())
            operations.append(current_op)
            current_op = None
            current_hunk = None
        elif line.startswith("@@"):
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                hint_match = re.match(r"@@\s*(.+?)\s*@@", line)
                hint = hint_match.group(1) if hint_match else None
                current_hunk = Hunk(context_hint=hint)
        elif current_op and line:
            if current_hunk is None:
                current_hunk = Hunk()
            if line.startswith("+"):
                current_hunk.lines.append(HunkLine("+", line[1:]))
            elif line.startswith("-"):
                current_hunk.lines.append(HunkLine("-", line[1:]))
            elif line.startswith(" "):
                current_hunk.lines.append(HunkLine(" ", line[1:]))
            elif not line.startswith("\\"):
                # Treat as implicit context line (no prefix)
                current_hunk.lines.append(HunkLine(" ", line))

        i += 1

    if current_op:
        if current_hunk and current_hunk.lines:
            current_op.hunks.append(current_hunk)
        operations.append(current_op)

    if not operations:
        return [], "No operations found in patch"

    parse_errors: list[str] = []
    for op in operations:
        if not op.file_path:
            parse_errors.append("operation with empty file path")
        if op.operation == OperationType.UPDATE and not op.hunks:
            parse_errors.append(f"UPDATE {op.file_path!r}: no hunks found")
    if parse_errors:
        return [], "Parse error: " + "; ".join(parse_errors)

    return operations, None
