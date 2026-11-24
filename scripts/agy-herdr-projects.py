#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time


session = "agy-projects"
workspace_label = "Antigravity Projects"
linux_home = Path.home()
bin_dir = linux_home / ".local" / "bin"
shared_skills = linux_home / ".local" / "share" / "agy-shared" / "skills"
state_dir = linux_home / ".local" / "state" / "agy-herdr-projects"
herdr_command = [str(bin_dir / "herdr"), "--session", session]
panes_per_tab = 4


@dataclass(frozen=True)
class Project:
    profile: str
    repo: Path
    label: str


def project_for(number):
    suffix = ""
    while number:
        number, letter = divmod(number - 1, 26)
        suffix = chr(ord("a") + letter) + suffix
    return Project(
        profile=f"acc-{suffix}",
        repo=linux_home / "projects" / f"agy-{suffix}",
        label=f"Project {suffix.upper()} | acc-{suffix}",
    )


def herdr(*args):
    result = subprocess.run(
        [*herdr_command, *args],
        check=True,
        text=True,
        capture_output=True,
    )
    if result.stdout.strip():
        return json.loads(result.stdout)["result"]


def list_workspaces():
    try:
        return herdr("workspace", "list")["workspaces"]
    except subprocess.CalledProcessError:
        log_path = state_dir / "server.log"
        with log_path.open("ab") as log:
            subprocess.Popen(
                [*herdr_command, "server"], stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True,
            )
        for _ in range(50):
            time.sleep(0.2)
            try:
                return herdr("workspace", "list")["workspaces"]
            except subprocess.CalledProcessError:
                continue
        raise SystemExit(f"Herdr failed to start. See {log_path}.")


def ensure_panes(workspace_id, projects):
    panes = herdr("pane", "list", "--workspace", workspace_id)["panes"]
    pane_ids = {pane["cwd"]: pane["pane_id"] for pane in panes}
    positions = ((0, "right"), (0, "right"), (1, "down"), (0, "down"))

    for offset in range(0, len(projects), panes_per_tab):
        group = projects[offset : offset + panes_per_tab]
        for index, project in enumerate(group):
            repo = str(project.repo)
            if repo in pane_ids:
                continue
            parent_index, direction = positions[index]
            parent = pane_ids.get(str(group[parent_index].repo))
            if parent is None:
                parent = next((pane_ids[str(item.repo)] for item in group if str(item.repo) in pane_ids), None)
            if parent is None:
                tab_label = f"Projects {offset + 1}-{offset + panes_per_tab}"
                result = herdr("tab", "create", "--workspace", workspace_id, "--cwd", repo, "--label", tab_label, "--no-focus")
                pane_id = result["root_pane"]["pane_id"]
            else:
                result = herdr("pane", "split", parent, "--direction", direction, "--cwd", repo, "--no-focus")
                pane_id = result["pane"]["pane_id"]
            pane_ids[repo] = pane_id
            herdr("pane", "rename", pane_id, project.label)


def launch_ready_panes(workspace_id, projects):
    by_repo = {str(project.repo): project for project in projects}
    panes = herdr("pane", "list", "--workspace", workspace_id)["panes"]
    windows_browser = Path("/mnt/c/Windows/explorer.exe")
    for pane in panes:
        project = by_repo.get(pane.get("cwd"))
        if project is None or pane.get("foreground_cwd") != str(project.repo):
            continue
        pane_id = pane["pane_id"]
        info = herdr("pane", "process-info", "--pane", pane_id)["process_info"]
        shell_pid = info.get("shell_pid")
        if shell_pid and info.get("foreground_process_group_id") == shell_pid:
            command = [str(bin_dir / "agys"), "run", project.profile]
            if windows_browser.is_file():
                command = ["env", f"BROWSER={windows_browser}", *command]
            herdr("pane", "run", pane_id, shlex.join(command))


def prepare_projects(projects):
    shared_skills.mkdir(parents=True, exist_ok=True)
    for project in projects:
        project.repo.mkdir(parents=True, exist_ok=True)
        profile_dir = linux_home / ".agys" / "profiles" / project.profile
        profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        skills_config = profile_dir / ".gemini" / "config" / "skills.json"
        skills_config.parent.mkdir(parents=True, exist_ok=True)
        if not skills_config.exists():
            manifest = {"entries": [{"path": str(shared_skills)}]}
            skills_config.write_text(json.dumps(manifest, indent=2) + "\n")

def choose_count(current):
    while True:
        entered = input(f"Total projects [{current}] (Enter to keep): ").strip()
        if not entered:
            return current
        if entered.isascii() and entered.isdecimal() and int(entered) >= current:
            return int(entered)
        print(f"Enter a whole number of at least {current}.")

def main():
    os.environ["PATH"] = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])
    parser = argparse.ArgumentParser(description="Open isolated Antigravity profiles in Herdr.")
    count_option = parser.add_mutually_exclusive_group()
    count_option.add_argument("--count", type=int, help="total number of projects to prepare")
    count_option.add_argument("--choose", action="store_true", help="ask for the total number of projects")
    parser.add_argument("--prepare", action="store_true", help="update the layout without attaching a client")
    args = parser.parse_args()
    if args.count is not None and args.count < 1:
        parser.error("--count must be positive")

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    count_path = state_dir / "project-count"
    current = int(count_path.read_text()) if count_path.exists() else 3
    requested = choose_count(current) if args.choose else args.count or current

    with (state_dir / "setup.lock").open("a") as setup_lock:
        fcntl.flock(setup_lock, fcntl.LOCK_EX)
        current = int(count_path.read_text()) if count_path.exists() else current
        total = max(current, requested)
        projects = [project_for(number) for number in range(1, total + 1)]
        prepare_projects(projects)
        workspaces = list_workspaces()
        workspace_id = next((item["workspace_id"] for item in workspaces if item.get("label") == workspace_label), None)
        if workspace_id is None:
            root = projects[0]
            created = herdr("workspace", "create", "--cwd", str(root.repo), "--label", workspace_label, "--focus")
            workspace_id = created["workspace"]["workspace_id"]
            herdr("pane", "rename", created["root_pane"]["pane_id"], root.label)
        ensure_panes(workspace_id, projects)
        launch_ready_panes(workspace_id, projects)
        herdr("workspace", "focus", workspace_id)
        temporary_count = count_path.with_suffix(".tmp")
        temporary_count.write_text(f"{total}\n")
        temporary_count.replace(count_path)

    if not args.prepare:
        os.execv(herdr_command[0], herdr_command)


if __name__ == "__main__":
    main()
