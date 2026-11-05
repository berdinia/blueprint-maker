"""``blueprint`` command-line interface — the single orchestration entry point.

Exit codes: 0 PASS, 1 FAIL, 2 usage/configuration error, 3 BLOCKED, 4 PARTIAL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .errors import BlueprintError, ConfigError

EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "PARTIAL": 4}


def _ctx(args: argparse.Namespace) -> Any:
    from .config.settings import load_settings
    from .orchestration.context import PipelineContext

    settings = load_settings(args.config, project_root=args.project)
    return PipelineContext(settings)


def _print_result(res: Any, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"[{res.status}] {res.stage}: {res.summary}")
    return EXIT.get(res.status, 1)


def cmd_stage(args: argparse.Namespace) -> int:
    from .orchestration.stages import run_stage

    ctx = _ctx(args)
    opts: dict[str, Any] = {}
    if args.command == "extract":
        opts = {"source_ids": args.source or None, "force": args.force}
    if args.command == "review":
        opts = {"simulated": args.simulated}
    return _print_result(run_stage(ctx, args.command, **opts), args.json)


def cmd_status(args: argparse.Namespace) -> int:
    from .orchestration.stages import pipeline_status

    st = pipeline_status(_ctx(args))
    if args.json:
        print(json.dumps(st, indent=2, sort_keys=True, default=str))
        return 0
    print(f"{'stage':<12} {'status':<9} {'state':<9} summary")
    for s in st["stages"]:
        print(f"{s['stage']:<12} {s['status']:<9} {s['freshness']:<9} {s['summary'][:90]}")
        for r in s.get("reasons", []):
            print(f"{'':<32}  ! {r}")
    r = st["review"]
    print(
        f"\nvisual review: {r['state']} ({r['reason']}); verdict {r['verdict']} by {r['reviewer']}"
    )
    lk = st["lock"]
    print(f"heavy-job lock: {lk['state']} - {lk['detail']}")
    if lk["record"] and lk["state"] != "FREE":
        print("  " + json.dumps(lk["record"], sort_keys=True))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from .orchestration.stages import resume

    results = resume(_ctx(args), simulated_review=args.simulated_review, until=args.until)
    if not results:
        print("all stages current; nothing to do")
        return 0
    code = 0
    for res in results:
        code = _print_result(res, args.json)
    return code


def cmd_lock(args: argparse.Namespace) -> int:
    from .orchestration.lock import clear_stale_lock, inspect_lock

    ctx = _ctx(args)
    path = ctx.settings.paths.lock_path
    if args.lock_cmd == "inspect":
        info = inspect_lock(path)
        print(json.dumps(info.__dict__, indent=2, sort_keys=True, default=str))
        return 0
    if not args.stale:
        print("refusing: pass --stale to confirm you are clearing a stale record", file=sys.stderr)
        return 2
    print(clear_stale_lock(path, allow_other_host=args.allow_other_host))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    s = ctx.settings
    print(json.dumps(s.redacted_dict(), indent=2, sort_keys=True))
    print(f"config_hash {s.config_hash()}")
    if args.probe:
        from .inference.client import LocalInferenceClient

        if s.inference.primary is None:
            print("probe: no endpoint configured")
            return 3
        client = LocalInferenceClient(s, s.models.extract or "probe")
        ok, detail = client.probe()
        print(f"probe primary: {'OK' if ok else 'FAILED'} ({detail})")
        if s.inference.native:
            ok2, d2 = client.probe(s.inference.native)
            print(f"probe native: {'OK' if ok2 else 'FAILED'} ({d2})")
        return 0 if ok else 3
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from .canonical.migrate import migrate_legacy_house_model
    from .io.atomic import atomic_write_json, read_json

    model = migrate_legacy_house_model(read_json(args.input))
    atomic_write_json(args.output, model)
    print(
        f"migrated legacy model -> {args.output} ({model['model_id']}); "
        f"{len(model['rooms'])} rooms marked hypothetical until re-confirmed"
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.project or ".").resolve()
    for d in ("input", "register", "work", "results"):
        (root / d).mkdir(parents=True, exist_ok=True)
    reg = root / "register" / "source-register.json"
    if not reg.exists():
        reg.write_text(
            json.dumps(
                {"schema": "blueprint.source-register", "schema_version": "1.0.0", "sources": []},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    cfg = root / "blueprint.yaml"
    if not cfg.exists():
        cfg.write_text(_EXAMPLE_CONFIG, encoding="utf-8")
    print(f"initialised project at {root}; edit blueprint.yaml and register/source-register.json")
    return 0


def cmd_run_synthetic(args: argparse.Namespace) -> int:
    from .testing.run import dump, report_markdown, run_synthetic
    from .testing.synthetic import write_fixture_files

    if args.export_fixture:
        for p in write_fixture_files(Path(args.export_fixture)):
            print(f"wrote {p}")
        return 0
    result = run_synthetic(Path(args.keep).resolve() if args.keep else None)
    if args.json:
        print(dump(result))
    else:
        print(report_markdown(result))
    if args.report:
        Path(args.report).write_text(report_markdown(result), encoding="utf-8")
    return 0 if result["passed"] else 1


_EXAMPLE_CONFIG = """\
# blueprint configuration (see docs/CONFIGURATION.md). Local-only by default.
schema_version: 1
paths:
  project_root: .
  input_dir: input        # original evidence (read-only)
  work_dir: work
  results_dir: results
privacy:
  mode: loopback          # loopback | private_network
inference:
  primary:
    url: http://127.0.0.1:11434
    api: ollama           # openai | ollama
  fallback_to_native: false
models:
  extract: null           # set to your local vision model
  review: null            # must differ from the extraction model
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueprint", description="Local-only blueprint evidence pipeline."
    )
    p.add_argument("--version", action="version", version=f"blueprint-pipeline {__version__}")
    p.add_argument("--config", "-c", help="YAML config file (or BLUEPRINT_CONFIG)")
    p.add_argument("--project", "-p", help="project root (overrides paths.project_root)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    # --json is accepted before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    _add = sub.add_parser

    def add_parser(name: str, **kw: Any) -> argparse.ArgumentParser:
        return _add(name, parents=[common], **kw)

    sub.add_parser = add_parser  # type: ignore[method-assign]
    for name, helptext in (
        ("inventory", "compare the source register with files on disk"),
        ("normalize", "write metadata-free, size-limited copies for the model"),
        ("extract", "run local extraction (heavy job; one at a time)"),
        ("classify", "source relationships, evidence origins, fragments"),
        ("reconcile", "room-identity candidates and conflicts"),
        ("build-model", "build the canonical model from evidence + decisions"),
        ("render", "deterministic DXF/SVG/PNG/PDF"),
        ("validate", "mechanical validation report"),
        ("review", "visual review with bounded repair (heavy job)"),
        ("package", "assemble the human-review packet"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.set_defaults(func=cmd_stage)
        if name == "extract":
            sp.add_argument("--source", action="append", help="limit to a source ID (repeatable)")
            sp.add_argument("--force", action="store_true", help="re-run even if current")
        if name == "review":
            sp.add_argument(
                "--simulated",
                action="store_true",
                help="deterministic SIMULATED reviewer (synthetic/testing; never independent)",
            )
    sp = sub.add_parser("status", help="stage status from artifact metadata")
    sp.set_defaults(func=cmd_status)
    sp = sub.add_parser("resume", help="run stages that are not current, in order")
    sp.add_argument("--simulated-review", action="store_true")
    sp.add_argument(
        "--until",
        choices=[
            "inventory",
            "normalize",
            "extract",
            "classify",
            "reconcile",
            "build-model",
            "render",
            "validate",
            "review",
            "package",
        ],
    )
    sp.set_defaults(func=cmd_resume)
    sp = sub.add_parser("lock", help="inspect or clear the heavy-job lock")
    lsub = sp.add_subparsers(dest="lock_cmd", required=True)
    lsub.add_parser("inspect", parents=[common])
    lc = lsub.add_parser("clear", parents=[common])
    lc.add_argument("--stale", action="store_true", help="confirm clearing a stale owner record")
    lc.add_argument("--allow-other-host", action="store_true")
    sp.set_defaults(func=cmd_lock)
    sp = sub.add_parser("config", help="show resolved (redacted) configuration")
    sp.add_argument("--probe", action="store_true", help="probe configured local endpoints")
    sp.set_defaults(func=cmd_config)
    sp = sub.add_parser(
        "migrate-model", help="convert a legacy house-model.json to canonical 1.0.0"
    )
    sp.add_argument("input")
    sp.add_argument("output")
    sp.set_defaults(func=cmd_migrate)
    sp = sub.add_parser("init", help="create an empty project skeleton")
    sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("run-synthetic", help="synthetic end-to-end run with output verification")
    sp.add_argument("--keep", metavar="DIR", help="keep the synthetic project in DIR")
    sp.add_argument("--report", metavar="FILE", help="write a Markdown report")
    sp.add_argument(
        "--export-fixture", metavar="DIR", help="only write the fixture JSON files to DIR"
    )
    sp.set_defaults(func=cmd_run_synthetic)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except BlueprintError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
