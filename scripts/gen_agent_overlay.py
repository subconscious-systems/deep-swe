#!/usr/bin/env python3
"""Generate Pier's agent-overlay Dockerfile for each task, for prebuilding.

Pier appends an agent-install overlay (apt deps + uv + `uv tool install
mini-swe-agent==X` + LiteLLM cost-map refresh) to every task image at TRIAL
time (pier/environments/docker/docker.py:_prepare_agent_build_context). On a
fresh box — or after changing the mini-swe-agent version pin — that means one
network-bound `docker compose build` per task image DURING the eval, competing
with the eval itself and able to fail on network blips.

This script writes the exact same Dockerfile Pier will generate, by calling
Pier's own write_agent_dockerfile()/install_spec() — byte-identical output, so
the layers prebuilt from it are guaranteed docker-cache hits at trial time
(the overlay has no COPY/ADD, so build context doesn't matter). It must run
under Pier's interpreter; scripts/prebuild.sh handles that:

    pier_python="$(head -1 "$(command -v pier)" | sed 's/^#!//')"
    "$pier_python" scripts/gen_agent_overlay.py --config <job.yaml> tasks/*/

Reads model_name and kwargs.version from the job YAML so the generated overlay
always matches what the eval will request (keep using the same --config you
run with). Emits one line per task to stdout:  <task>\t<dockerfile_dir>
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from pathlib import Path

import yaml

from pier.agents.installed.mini_swe_agent import MiniSweAgent
from pier.environments.agent_setup import write_agent_dockerfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="pier job yaml (e.g. mini-swe-agent/full-spark.yaml)")
    ap.add_argument("--out-dir", default="/tmp/deepswe-agent-overlays")
    ap.add_argument("task_dirs", nargs="+")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    agent_cfg = cfg["agents"][0]
    kwargs = agent_cfg.get("kwargs") or {}

    # Same constructor surface Pier's factory uses; only model_name and
    # version influence install_spec() (model_name only via the vertex_ai/
    # google-auth special case).
    with tempfile.TemporaryDirectory() as tmp:
        agent = MiniSweAgent(
            logs_dir=Path(tmp),
            model_name=agent_cfg.get("model_name"),
            version=kwargs.get("version"),
        )
        install = agent.install_spec()

    out_root = Path(args.out_dir)
    for task_dir in args.task_dirs:
        task_dir = Path(task_dir.rstrip("/"))
        task = task_dir.name
        toml_path = task_dir / "task.toml"
        if not toml_path.is_file():
            print(f"SKIP {task}: no task.toml", file=sys.stderr)
            continue
        task_cfg = tomllib.loads(toml_path.read_text())
        env_cfg = task_cfg.get("environment") or {}
        docker_image = env_cfg.get("docker_image")
        if not docker_image:
            print(f"SKIP {task}: no [environment].docker_image", file=sys.stderr)
            continue
        build_dir = out_root / task
        write_agent_dockerfile(
            build_dir=build_dir,
            source_environment_dir=build_dir,  # unused when prebuilt_image_name is set
            prebuilt_image_name=docker_image,
            install=install,
            # Trials pass _resolve_user(None) -> environment.default_user, which
            # is None for tasks that don't set [environment].user (all of ours).
            user=env_cfg.get("user"),
        )
        print(f"{task}\t{build_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
