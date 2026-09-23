"""`Workspace` = the design core + collaboration + results + runs, as one application object."""

from __future__ import annotations

from .collab import CollabMixin
from .core import WorkspaceCore, WorkspaceError, new_id
from .results import ResultsMixin
from .runs import RunsMixin

__all__ = ["Workspace", "WorkspaceError", "new_id"]


class Workspace(CollabMixin, ResultsMixin, RunsMixin, WorkspaceCore):
    """The application layer every door (Studio, MCP, CLI, file import) calls."""

    def _after_init(self) -> None:
        self._results_init()

    def __init__(self, root, *, release_dirs=None, recover: bool = False):
        super().__init__(root, release_dirs=release_dirs, recover=recover)

    def close(self) -> None:
        self._closed = True               # stops the cancellation watchers before the database closes
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        super().close()
