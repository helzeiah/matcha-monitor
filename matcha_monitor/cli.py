from __future__ import annotations

import click
import logging
from pathlib import Path

"""
Commands to add:
    matcha-monitor --help
    matcha-monitor --version
    matcha-monitor --list
    matcha-monitor --add <name> <url>
    matcha-monitor --remove <name>
    matcha-monitor --run
    matcha-monitor --init
"""


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _echo_ok(message: str) -> None:
    click.secho(message, fg="green")


def _echo_warn(message: str) -> None:
    click.secho(message, fg="yellow")


def _echo_err(message: str) -> None:
    click.secho(message, fg="red")


@click.group()
@click.option("--config", type=click.Path(dir_okay=False, path_type=Path),
              default=Path("config.yaml"), show_default=True, help="Path to config file.")
@click.option("--verbose", is_flag=True, help="Enable debug logs.")
@click.version_option(package_name="matcha-monitor")
@click.pass_context
def cli(ctx: click.Context, config: Path, verbose: bool):
    """matcha-monitor: get restock alerts for matcha drops from your favorite provider."""
    ctx.ensure_object(dict)
    ctx.obj["CONFIG_PATH"] = config
    ctx.obj["VERBOSE"] = verbose
    _setup_logging(verbose)
    logging.debug("Using config at %s", config)


@cli.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing files if present.")
@click.pass_context
def init(ctx: click.Context, force: bool) -> None:
    from matcha_monitor.core import config as cfg
    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    try:
        created = cfg.init_scaffold(cfg_path, force=force)
    except FileExistsError:
        _echo_warn(f"{cfg_path} already exists. Use --force to overwrite.")
        raise SystemExit(1)

    _echo_ok(f"Created {created.config} and {created.env_example}")
    click.echo("Next: open the config, add a target, then run `matcha-monitor test <name>`.")


@cli.command("add")
@click.argument("name", metavar="<name>")
@click.argument("url", metavar="<url>")
@click.option("--method", type=click.Choice(["dom", "json"]), default="dom", show_default=True)
@click.option("--rule", default="", help="CSS selector (dom) or JSON path (json).")
@click.option("--expect", default="", help="Condition, e.g., text:'add to cart' or bool:true.")
@click.option("--interval", default="60s", show_default=True, help='e.g., "45s", "2m".')
@click.pass_context
def add_cmd(ctx, name, url, method, rule, expect, interval):
    from matcha_monitor.core import config as cfg

    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    try:
        data = cfg.load_config(cfg_path)
        data = cfg.add_target(
            data, name=name, url=url, method=method,
            rule=rule, expect=expect, interval=interval
        )
        cfg.save_config(data, cfg_path)
    except cfg.ConfigError as e:
        _echo_err(str(e))
        raise SystemExit(1)
    except FileNotFoundError:
        _echo_err(f"No config at {cfg_path}. Run `matcha-monitor init` first.");
        raise SystemExit(1)

    _echo_ok(f"Added '{name}' → {url}")


@cli.command("remove")
@click.argument("name", metavar="<name>")
@click.pass_context
def remove_cmd(ctx, name):
    from matcha_monitor.core import config as cfg

    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    try:
        data = cfg.load_config(cfg_path)
        removed = cfg.remove_target(data, name=name)
        if not removed:
            _echo_warn(f"Target '{name}' not found.")
            raise SystemExit(1)
        cfg.save_config(data, cfg_path)
    except FileNotFoundError:
        _echo_err(f"No config at {cfg_path}. Run `matcha-monitor init` first.");
        raise SystemExit(1)
    except cfg.ConfigError as e:
        _echo_err(str(e));
        raise SystemExit(1)

    _echo_ok(f"Removed '{name}'")


@cli.command("list")
@click.pass_context
def list_cmd(ctx):
    from matcha_monitor.core import config as cfg, state as st

    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    try:
        data = cfg.load_config(cfg_path)
    except FileNotFoundError:
        _echo_err(f"No config at {cfg_path}. Run `matcha-monitor init` first.")
        raise SystemExit(1)

    try:
        status = st.load_state(Path("state.json"))
    except FileNotFoundError:
        status = {}

    targets = data.get("targets", [])
    if not targets:
        _echo_warn("No targets configured. Use `matcha-monitor add <name> <url>`.")
        return

    click.echo("Name                         Method  Interval  Last Status     Last Change")
    click.echo("-" * 78)
    for t in targets:
        name = t.get("name", "")
        method = t.get("method", "")
        interval = t.get("interval", "")
        s = status.get(name, {})
        last_val = s.get("last_value", "—")
        last_chg = s.get("last_changed_at", "—")
        click.echo(f"{name:28} {method:6}  {interval:8} {str(last_val):13} {last_chg}")


@cli.command("test")
@click.argument("name", metavar="<name>")
@click.pass_context
def test_cmd(ctx, name):
    from matcha_monitor.core import runner, config as cfg, state as st

    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    try:
        conf = cfg.load_config(cfg_path)
        stobj = st.safe_load_or_empty(Path("state.json"))
        result = runner.test_target(conf, stobj, name=name)
    except FileNotFoundError:
        _echo_err(f"No config at {cfg_path}. Run `matcha-monitor init` first.");
        raise SystemExit(1)
    except runner.TargetNotFound as e:
        _echo_err(str(e))
        raise SystemExit(1)
    except Exception as e:
        _echo_err(f"Test failed: {e}")
        raise SystemExit(2)

    click.echo(
        f"value={result.value!r} in_stock={result.in_stock} would_alert={result.would_alert}")


@cli.command("run")
@click.option("--once", is_flag=True, help="Run one pass and exit.")
@click.option("--dry-run", is_flag=True, help="Do everything except send SMS.")
@click.option("--only", metavar="<name>", help="Run for a single target.")
@click.pass_context
def run_cmd(ctx, once, dry_run, only):
    from matcha_monitor.core import runner, config as cfg, state as st

    cfg_path: Path = ctx.obj["CONFIG_PATH"]
    verbose = ctx.obj["VERBOSE"]

    try:
        conf = cfg.load_config(cfg_path)
        stpath = Path("state.json")
        stobj = st.safe_load_or_empty(stpath)
        runner.run_loop(
            conf, stobj, state_path=stpath,
            once=once, dry_run=dry_run, only=only, verbose=verbose,
        )
    except FileNotFoundError:
        _echo_err(f"No config at {cfg_path}. Run `matcha-monitor init` first.");
        raise SystemExit(1)
    except Exception as e:
        _echo_err(f"Run failed: {e}")
        raise SystemExit(2)
