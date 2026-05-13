"""File read state tracker — mtime registry and partial-read flag per resolved path."""


class FileReadTracker:
    def __init__(self) -> None:
        self._mtimes: dict[str, float] = {}
        self._partial_reads: set[str] = set()

    def record_read(self, path_key: str, mtime: float, partial: bool = False) -> None:
        self._mtimes[path_key] = mtime
        if partial:
            self._partial_reads.add(path_key)
        else:
            self._partial_reads.discard(path_key)

    def is_read(self, path_key: str) -> bool:
        return path_key in self._mtimes

    def is_partial(self, path_key: str) -> bool:
        return path_key in self._partial_reads

    def is_stale(self, path_key: str, current_mtime: float) -> bool:
        # Only meaningful when is_read() is True — caller must check is_read() first.
        return self._mtimes.get(path_key) != current_mtime

    def is_read_and_stale(self, path_key: str, current_mtime: float) -> bool:
        # Allows never-read files (no entry) but rejects reads whose mtime changed.
        return path_key in self._mtimes and self._mtimes[path_key] != current_mtime

    def update_mtime(self, path_key: str, mtime: float) -> None:
        self._mtimes[path_key] = mtime
