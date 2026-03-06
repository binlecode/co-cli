"""Workspace checkpoint and rewind — git-backed (stash) or filesystem fallback."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _git_available(workspace_root: Path) -> bool:
    """Return True if workspace_root has a .git directory and git is on PATH."""
    return (workspace_root / ".git").exists() and shutil.which("git") is not None


def create_checkpoint(workspace_root: Path, label: str = "") -> str:
    """Create a workspace snapshot. Returns the checkpoint ID.

    Uses git stash when .git exists, filesystem copy otherwise.
    """
    checkpoint_id = uuid4().hex[:8]
    timestamp = datetime.now(timezone.utc).isoformat()

    if _git_available(workspace_root):
        stash_message = f"co-cli:{checkpoint_id}"
        if label:
            stash_message = f"co-cli:{checkpoint_id}:{label}"
        result = subprocess.run(
            ["git", "stash", "push", "-u", "-m", stash_message],
            cwd=workspace_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git stash failed: {result.stderr.strip()}")
        backend = "git"
    else:
        # Filesystem fallback: copy workspace files to .co-cli/checkpoints/<id>/
        checkpoints_dir = workspace_root / ".co-cli" / "checkpoints" / checkpoint_id
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        for item in workspace_root.iterdir():
            # Skip hidden dirs like .co-cli itself and .git
            if item.name.startswith("."):
                continue
            dest = checkpoints_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        # Save metadata
        meta = {
            "id": checkpoint_id,
            "label": label,
            "created_at": timestamp,
            "backend": "filesystem",
        }
        (checkpoints_dir / ".checkpoint_meta.json").write_text(json.dumps(meta, indent=2))
        backend = "filesystem"

    # Write a lightweight index entry for list_checkpoints
    _append_checkpoint_index(workspace_root, checkpoint_id, label, timestamp, backend)
    return checkpoint_id


def list_checkpoints(workspace_root: Path) -> list[dict]:
    """Return [{id, label, created_at, backend}] most-recent-first."""
    index_path = workspace_root / ".co-cli" / "checkpoints.json"
    if not index_path.exists():
        return []
    try:
        entries = json.loads(index_path.read_text())
        return list(reversed(entries))
    except Exception:
        return []


def restore_checkpoint(workspace_root: Path, checkpoint_id: str, *, confirmed: bool = False) -> None:
    """Restore a workspace to a previous checkpoint.

    Raises RuntimeError if confirmed=False (no write without confirmation).
    Raises RuntimeError if checkpoint_id not found.
    """
    if not confirmed:
        raise RuntimeError("restore_checkpoint requires confirmed=True")

    entries = list_checkpoints(workspace_root)
    match = next((e for e in entries if e["id"] == checkpoint_id), None)
    if match is None:
        raise RuntimeError(f"Checkpoint not found: {checkpoint_id}")

    backend = match.get("backend", "unknown")

    if backend == "git":
        # Find the stash ref by matching message pattern
        result = subprocess.run(
            ["git", "stash", "list"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git stash list failed: {result.stderr.strip()}")
        stash_ref = None
        for line in result.stdout.splitlines():
            if f"co-cli:{checkpoint_id}" in line:
                # Line format: stash@{N}: On branch: message
                stash_ref = line.split(":")[0].strip()
                break
        if stash_ref is None:
            raise RuntimeError(f"Git stash for checkpoint {checkpoint_id} not found")
        pop_result = subprocess.run(
            ["git", "stash", "pop", stash_ref],
            cwd=workspace_root,
            capture_output=True,
            text=True,
        )
        if pop_result.returncode != 0:
            raise RuntimeError(f"git stash pop failed: {pop_result.stderr.strip()}")

    elif backend == "filesystem":
        checkpoints_dir = workspace_root / ".co-cli" / "checkpoints" / checkpoint_id
        if not checkpoints_dir.exists():
            raise RuntimeError(f"Checkpoint directory not found: {checkpoints_dir}")
        # Restore: copy files back (overwrite), skipping hidden dirs
        for item in checkpoints_dir.iterdir():
            if item.name == ".checkpoint_meta.json":
                continue
            dest = workspace_root / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    else:
        raise RuntimeError(f"Unknown checkpoint backend: {backend}")


def _append_checkpoint_index(
    workspace_root: Path, checkpoint_id: str, label: str, timestamp: str, backend: str
) -> None:
    """Append a checkpoint entry to .co-cli/checkpoints.json."""
    index_path = workspace_root / ".co-cli" / "checkpoints.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    if index_path.exists():
        try:
            entries = json.loads(index_path.read_text())
        except Exception:
            entries = []
    entries.append({
        "id": checkpoint_id,
        "label": label,
        "created_at": timestamp,
        "backend": backend,
    })
    index_path.write_text(json.dumps(entries, indent=2))
