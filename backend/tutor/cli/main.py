"""Typer CLI entry point — ``tutor ...``.

Subcommands:

- ``tutor info``              — show configuration
- ``tutor capabilities``      — list capabilities
- ``tutor tools``             — list tools
- ``tutor system-check``      — verify dependencies
- ``tutor chat``              — start an interactive chat (placeholder)
- ``tutor config ...``        — manage model catalog (placeholder)
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import typer
from rich.console import Console
from rich.table import Table

from tutor import __version__
from tutor.runtime import get_capability_registry, get_tool_registry
from tutor.services.config.settings import get_settings

app = typer.Typer(
    name="tutor",
    help="TutorBot — 个性化学习资源生成多智能体系统 CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Tutor v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="显示版本",
    ),
) -> None:
    """TutorBot — Multi-Agent Learning System."""


@app.command("info")
def info_cmd() -> None:
    """显示当前配置。"""
    settings = get_settings()
    table = Table(title="Tutor Configuration", show_header=True, header_style="bold")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    rows = [
        ("env", settings.env),
        ("language", settings.language),
        ("host:port", f"{settings.host}:{settings.port}"),
        ("llm.provider", settings.llm_provider),
        ("llm.model", settings.llm_model),
        ("llm.api_key", "***set***" if settings.llm_api_key else "(empty)"),
        ("llm.base_url", settings.llm_base_url),
        ("rag.provider", settings.rag_provider),
        ("kb.default", settings.kb_default),
        ("manim.enabled", str(settings.manim_enabled)),
        ("manim.quality", settings.manim_quality),
    ]
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(table)


@app.command("capabilities")
def capabilities_cmd() -> None:
    """列出所有已注册的能力。"""
    caps = get_capability_registry()
    table = Table(title="Capabilities", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Stages", style="dim")

    for manifest in caps.get_manifests():
        table.add_row(
            manifest["name"],
            manifest["description"],
            ", ".join(manifest.get("stages", [])),
        )
    console.print(table)


@app.command("tools")
def tools_cmd() -> None:
    """列出所有已注册的工具。"""
    tools = get_tool_registry()
    table = Table(title="Tools", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for name in tools.list_tools():
        t = tools.get(name)
        if t is not None:
            table.add_row(name, t.description)
    console.print(table)


@app.command("system-check")
def system_check_cmd() -> None:
    """检查外部依赖（manim、ffmpeg、git...）。"""
    rows = []
    rows.append(("python", sys.version.split()[0]))

    for cmd, args in [
        ("manim", ["--version"]),
        ("ffmpeg", ["-version"]),
        ("git", ["--version"]),
        ("node", ["--version"]),
    ]:
        if shutil.which(cmd) is None:
            rows.append((cmd, "❌ not installed"))
            continue
        try:
            out = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=10)
            first = (out.stdout or out.stderr).splitlines()
            rows.append((cmd, first[0] if first else "(no output)"))
        except Exception as exc:  # noqa: BLE001
            rows.append((cmd, f"❌ error: {exc}"))

    table = Table(title="System Check", show_header=True, header_style="bold")
    table.add_column("Tool", style="cyan")
    table.add_column("Status")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


@app.command("chat")
def chat_cmd() -> None:
    """启动交互式聊天（占位 — Phase 2 实现完整 TUI）。"""
    console.print("[yellow]chat[/yellow] 占位实现 — Phase 2 完整化")
    console.print("当前可通过 Web 界面或 WebSocket 客户端测试。")


@app.command("api")
def api_cmd() -> None:
    """启动 FastAPI 服务（同 ``python -m tutor api``）。"""
    from tutor.api.run_server import run

    run()


if __name__ == "__main__":
    app()
