from __future__ import annotations

import asyncio
import importlib.util
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskProgressColumn, TextColumn
from rich.table import Table

from crawlerflow import __version__
from crawlerflow.engine.executor import WorkflowExecutionError
from crawlerflow.engine.runner import WorkflowRunner
from crawlerflow.plugins import discover_plugins
from crawlerflow.workflow.loader import WorkflowLoadError

app = typer.Typer(help="Declarative browser automation workflow engine.", no_args_is_help=True)
console = Console()
workflow_errors = (WorkflowLoadError, WorkflowExecutionError, ValidationError, ValueError)


class RunMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


@app.command()
def run(
    workflows: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=True,
            readable=True,
            help="Workflow YAML files or directories containing YAML files.",
        ),
    ],
    mode: Annotated[
        RunMode,
        typer.Option("--mode", "-m", help="Workflow execution mode."),
    ] = RunMode.SYNC,
    show_progress: Annotated[
        bool,
        typer.Option("--progress", help="Show a live workflow progress bar."),
    ] = False,
    concurrency: Annotated[
        int | None,
        typer.Option(
            "--concurrency",
            "-c",
            min=1,
            help="Maximum workflows running at once in async mode.",
        ),
    ] = None,
) -> None:
    """Run one or more YAML workflows sequentially or in parallel."""

    workflow_paths = _expand_workflow_paths(workflows)
    if not workflow_paths:
        console.print("[red]No workflow YAML files found.[/red]")
        raise typer.Exit(1)

    if mode is RunMode.ASYNC:
        _run_async_workflows(
            workflow_paths,
            show_progress=show_progress,
            concurrency=concurrency,
        )
        return

    if concurrency is not None:
        console.print("[red]--concurrency requires --mode async.[/red]")
        raise typer.Exit(1)
    _run_sync_workflows(workflow_paths, show_progress=show_progress)


def _expand_workflow_paths(paths: list[Path]) -> list[Path]:
    workflows: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        candidates = (
            sorted(
                (
                    child
                    for child in path.iterdir()
                    if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}
                ),
                key=lambda child: (child.name.casefold(), child.name),
            )
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            identity = candidate.resolve()
            if identity not in seen:
                seen.add(identity)
                workflows.append(candidate)
    return workflows


def _run_sync_workflows(workflows: list[Path], *, show_progress: bool = False) -> None:
    with _create_workflow_progress(show_progress) as progress:
        progress_task = progress.add_task("Workflows", total=len(workflows))
        for workflow in workflows:
            try:
                context = asyncio.run(WorkflowRunner().run(workflow))
            except workflow_errors as error:
                progress.advance(progress_task)
                console.print(f"[red]Workflow failed:[/red] {workflow}: {error}")
                raise typer.Exit(1) from error
            progress.advance(progress_task)
            console.print(f"[green]Completed:[/green] {context.workflow_name}")


def _run_async_workflows(
    workflows: list[Path],
    *,
    show_progress: bool = False,
    concurrency: int | None = None,
) -> None:
    async def run_one(
        workflow: Path,
        semaphore: asyncio.Semaphore | None,
    ) -> tuple[Path, object]:
        try:
            if semaphore is None:
                result = await WorkflowRunner().run(workflow)
            else:
                async with semaphore:
                    result = await WorkflowRunner().run(workflow)
            return workflow, result
        except BaseException as error:
            return workflow, error

    async def run_all(
        progress: Progress,
        progress_task: int,
    ) -> tuple[bool, BaseException | None]:
        failed = False
        unexpected_error: BaseException | None = None
        semaphore = asyncio.Semaphore(concurrency) if concurrency is not None else None
        tasks = [
            asyncio.create_task(run_one(workflow, semaphore)) for workflow in workflows
        ]
        for completed in asyncio.as_completed(tasks):
            workflow, result = await completed
            progress.advance(progress_task)
            if isinstance(result, workflow_errors):
                failed = True
                console.print(f"[red]Workflow failed:[/red] {workflow}: {result}")
            elif isinstance(result, BaseException):
                unexpected_error = unexpected_error or result
            else:
                console.print(f"[green]Completed:[/green] {result.workflow_name}")
        return failed, unexpected_error

    with _create_workflow_progress(show_progress) as progress:
        progress_task = progress.add_task("Workflows", total=len(workflows))
        failed, unexpected_error = asyncio.run(run_all(progress, progress_task))
    if unexpected_error is not None:
        raise unexpected_error
    if failed:
        raise typer.Exit(1)


def _create_workflow_progress(enabled: bool) -> Progress:
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        console=console,
        disable=not enabled,
        refresh_per_second=10,
    )


@app.command()
def validate(
    workflow: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True),
    ],
) -> None:
    """Validate workflow structure and step configurations."""

    try:
        document = WorkflowRunner().load(workflow)
    except (WorkflowLoadError, ValidationError, ValueError) as error:
        console.print(f"[red]Invalid workflow:[/red] {error}")
        raise typer.Exit(1) from error
    console.print(f"[green]Valid workflow:[/green] {document.workflow.name}")


@app.command("list-steps")
def list_steps() -> None:
    """List all registered workflow steps."""

    runner = WorkflowRunner()
    table = Table("Step")
    for name in runner.registry.names():
        table.add_row(name)
    console.print(table)


@app.command("list-plugins")
def list_plugins() -> None:
    """List installed Crawlerflow plugin entry points without loading them."""

    plugins = discover_plugins()
    if not plugins:
        console.print("[yellow]No plugins installed.[/yellow]")
        return

    table = Table("Plugin", "Target", "Distribution", box=None, pad_edge=False)
    for plugin in plugins:
        table.add_row(plugin.name, plugin.target, plugin.distribution or "-")
    console.print(table)


@app.command()
def doctor() -> None:
    """Display runtime and package diagnostics."""

    table = Table("Check", "Value")
    table.add_row("Crawlerflow", __version__)
    table.add_row("Python", sys.version.split()[0])
    table.add_row("Runtime", "OK" if sys.version_info >= (3, 12) else "Python 3.12+ required")
    pydoll_status = "Installed" if importlib.util.find_spec("pydoll") else "Not installed"
    table.add_row("Pydoll adapter", pydoll_status)
    console.print(table)
