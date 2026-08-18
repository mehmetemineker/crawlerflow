from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from crawlerflow.workflow.models import WorkflowDocument


class WorkflowLoadError(ValueError):
    """Raised when a workflow cannot be read or validated."""


class WorkflowLoader:
    """Load and validate YAML workflow documents."""

    def load(self, path: str | Path) -> WorkflowDocument:
        workflow_path = Path(path)
        try:
            raw: Any = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise WorkflowLoadError(f"Cannot read workflow: {workflow_path}") from error
        except yaml.YAMLError as error:
            raise WorkflowLoadError(f"Invalid YAML in {workflow_path}: {error}") from error

        if raw is None:
            raise WorkflowLoadError(f"Workflow is empty: {workflow_path}")
        try:
            return WorkflowDocument.model_validate(raw)
        except ValidationError as error:
            raise WorkflowLoadError(str(error)) from error

