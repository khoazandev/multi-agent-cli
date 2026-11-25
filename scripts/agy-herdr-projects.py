#!/usr/bin/env python3

import argparse
from dataclasses import asdict, dataclass, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

from herdr_layout import create_project_tab


linux_home = Path(os.environ.get("AGYS_REAL_HOME") or Path.home())
bin_dir = linux_home / ".local" / "bin"
library = linux_home / ".local" / "lib" / "multi-agent-cli"
state_dir = linux_home / ".local" / "state" / "agy-herdr-projects"
shared_skills = linux_home / ".local" / "share" / "agy-shared" / "skills"
legacy_session = "agy-projects"
workspace_label = "Antigravity Projects"
starter_label = "[Multi Agent CLI] Setup"
panes_per_tab = 4
identifier = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
marker = re.compile(r" \[project:([a-zA-Z0-9_-]+)\]$")
conversation_id = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    repo: Path
    profiles: tuple[str, ...]

    @property
    def profile(self):
        return self.profiles[0]

    @property
    def pane_label(self):
        return f"{self.name} | {self.profile} [project:{self.id}]"


def project_for(number):
    suffix = ""
    while number:
        number, letter = divmod(number - 1, 26)
        suffix = chr(ord("a") + letter) + suffix
    return Project(f"project-{suffix}", f"Project {suffix.upper()}",
                   linux_home / "projects" / f"agy-{suffix}", (f"acc-{suffix}",))


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def load_projects(initial_count=None):
    manifest = state_dir / "projects.json"
    if manifest.exists():
        try:
            items = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(items, list):
                raise ValueError("expected a list")
            projects = []
            for item in items:
                if not isinstance(item["profiles"], list):
                    raise ValueError("expected a list of profiles")
                projects.append(Project(item["id"], item["name"],
                                        Path(item["repo"]).expanduser(), tuple(item["profiles"])))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Cannot read {manifest}: {error}") from error
        validate_projects(projects)
        return projects

    try:
        legacy_count = max(0, int((state_dir / "project-count").read_text()))
    except (FileNotFoundError, ValueError):
        legacy_count = 0
    total = max(legacy_count, initial_count or 0) or 3
    return [project_for(number) for number in range(1, total + 1)]


def validate_projects(projects):
    ids, repos, profiles = set(), set(), set()
    for project in projects:
        if not isinstance(project.id, str) or not identifier.fullmatch(project.id):
            raise ValueError(f"Invalid project ID: {project.id!r}")
        if not isinstance(project.name, str) or not project.name.strip() or not project.name.isprintable():
            raise ValueError(f"Invalid name for project {project.id}")
        if not project.repo.is_absolute():
            raise ValueError(f"Use an absolute repository path for {project.id}")
        repo = project.repo.resolve()
        if project.id in ids or repo in repos:
            raise ValueError(f"Duplicate project ID or repository: {project.id} ({repo})")
        ids.add(project.id)
        repos.add(repo)
        if not project.profiles:
            raise ValueError(f"Project {project.id} needs a profile")
        for profile in project.profiles:
            if not isinstance(profile, str) or not identifier.fullmatch(profile) or profile.lower() == "auto":
                raise ValueError(f"Invalid profile for {project.id}: {profile!r}")
            if profile in profiles:
                raise ValueError(f"The profile {profile!r} is already assigned")
            profiles.add(profile)


def save_projects(projects):
    write_json(state_dir / "projects.json", [
        {**asdict(project), "repo": str(project.repo)} for project in projects
    ])


def accounts_root():
    path = Path(os.environ.get("AGYS_DIR") or linux_home / ".agys").expanduser()
    if not path.is_absolute():
        raise ValueError("AGYS_DIR must be an absolute path")
    configured = state_dir / "profiles-root.json"
    if configured.exists():
        try:
            existing = Path(json.loads(configured.read_text(encoding="utf-8")))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid profile root in {configured}") from error
        if not existing.is_absolute():
            raise ValueError(f"Profile root in {configured} must be an absolute path")
        if os.environ.get("AGYS_DIR") and path.resolve() != existing:
            raise ValueError(f"AGYS_DIR differs from the workbench profile root in {configured}")
        return existing
    return path.resolve()


def prepare_projects(projects, root):
    shared_skills.mkdir(parents=True, exist_ok=True)
    for project in projects:
        project.repo.mkdir(parents=True, exist_ok=True)
        for profile in project.profiles:
            profile_dir = root / "profiles" / profile
            profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            manifest = profile_dir / ".gemini" / "config" / "skills.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = json.loads(manifest.read_text(encoding="utf-8"))
            except FileNotFoundError:
                content = {}
            except ValueError as error:
                raise ValueError(f"Invalid skill manifest at {manifest}: {error}") from error
            if not isinstance(content, dict) or not isinstance(content.get("entries", []), list):
                raise ValueError(f"Invalid skill manifest at {manifest}")
            entries = content.setdefault("entries", [])
            if not any(isinstance(entry, dict) and entry.get("path") == str(shared_skills) for entry in entries):
                entries.append({"path": str(shared_skills)})
                write_json(manifest, content)


def match_panes(projects, panes, bindings):
    known = {project.id for project in projects}
    current = {terminal: project for project, terminal in bindings.items() if project in known}
    matched = {}
    for pane in panes:
        label = marker.search(pane.get("label") or "")
        project_id = current.get(pane["terminal_id"]) or (label.group(1) if label else None)
        if project_id not in known:
            continue
        if project_id in matched:
            raise ValueError(f"Multiple panes are marked as {project_id}. Close the extra managed pane and retry.")
        matched[project_id] = pane
    return matched


def pane_bindings():
    try:
        bindings = json.loads((state_dir / "panes.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(bindings, dict):
        return {}
    return {project: terminal for project, terminal in bindings.items() if isinstance(terminal, str)}


def client_environment():
    env = dict(os.environ)
    for variable in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_SESSION"):
        env.pop(variable, None)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)])
    return env


def server_environment():
    env = client_environment()
    config = state_dir / "config"
    settings = config / "herdr" / "config.toml"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if not settings.exists():
        settings.write_text("onboarding = false\n\n[session]\nresume_agents_on_restore = false\n"
                            "\n[update]\nversion_check = false\n", encoding="utf-8")
    env["XDG_CONFIG_HOME"] = str(config)
    env["XDG_STATE_HOME"] = str(state_dir / "herdr-state")
    env["HERDR_CONFIG_PATH"] = str(settings)
    env["HERDR_SOCKET_PATH"] = str(runtime_socket())
    env["MULTI_AGENT_CLI_SESSION"] = "1"
    return env


def runtime_socket():
    # Keep both Herdr socket names below the Unix path limit even with a long home path.
    fingerprint = hashlib.sha256(os.fsencode(state_dir.resolve())).hexdigest()[:16]
    base = Path(f"/run/user/{os.getuid()}")
    if not base.is_dir():
        base = Path("/tmp")
    directory = base / f"multi-agent-cli-{os.getuid()}-{fingerprint}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Unexpected ownership or permissions for {directory}")
    return directory / "h.sock"


def herdr_command(name=None):
    return [str(library / "herdr"), *(("--session", name) if name else ())]


def herdr(env, *args, name=None):
    result = subprocess.run([*herdr_command(name), *args], env=env, text=True,
                            capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Herdr command failed: {args[0]}")
    response = json.loads(result.stdout) if result.stdout.strip() else {}
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    return response.get("result", response)


def legacy_running():
    return herdr(client_environment(), "status", "server", "--json", name=legacy_session).get("running", False)


def start_server(env):
    status = herdr(env, "status", "server", "--json")
    if status["running"]:
        return False
    log_path = state_dir / "server.log"
    with log_path.open("ab") as log:
        subprocess.Popen([*herdr_command(), "server"], env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        try:
            herdr(env, "workspace", "list")
            return True
        except RuntimeError:
            continue
    raise RuntimeError(f"Herdr failed to start. See {log_path}.")


def session_file(project, profile):
    return state_dir / "conversations" / project.id / profile


def pane_command(project, fresh):
    command = [str(bin_dir / "agy-herdr-projects"), "run", project.profile]
    if not fresh:
        try:
            latest = session_file(project, project.profile).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return command
        if conversation_id.fullmatch(latest):
            command += ["--", f"--conversation={latest}"]
    return command


def find_workspace(env):
    return next((item["workspace_id"] for item in herdr(env, "workspace", "list")["workspaces"]
                 if item.get("label") == workspace_label), None)


def sync_panes(env, projects, starting, fresh):
    workspace_id = find_workspace(env)
    if workspace_id is None:
        created = herdr(env, "workspace", "create", "--cwd", str(linux_home), "--label", workspace_label, "--focus")
        workspace_id = created["workspace"]["workspace_id"]
        herdr(env, "pane", "rename", created["root_pane"]["pane_id"], starter_label)

    bindings = pane_bindings()
    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
    matched = match_panes(projects, panes, bindings)
    placeholders = [pane["pane_id"] for pane in panes if pane.get("label") == starter_label]
    # Dedicated Herdr restores layout and starts shells. Reopen managed agents from their
    # own saved profile pointers before attaching a client. Leave live PTYs alone.
    if starting:
        placeholders.extend(pane["pane_id"] for pane in matched.values())
        matched.clear()

    missing = [project for project in projects if project.id not in matched]
    socket_path = env["HERDR_SOCKET_PATH"]
    for offset in range(0, len(missing), panes_per_tab):
        group = missing[offset:offset + panes_per_tab]
        label = group[0].name if len(group) == 1 else f"{group[0].name} – {group[-1].name}"
        create_project_tab(socket_path, workspace_id, label, [{
            "repo": project.repo,
            "label": project.pane_label,
            "command": pane_command(project, fresh),
        } for project in group])

    if len(matched) + len(missing) > 0:
        for pane_id in placeholders:
            herdr(env, "pane", "close", pane_id)
    final = match_panes(projects, herdr(env, "pane", "list", "--workspace", workspace_id)["panes"], bindings)
    write_json(state_dir / "panes.json", {project: pane["terminal_id"] for project, pane in final.items()})
    return workspace_id


def run_profile(profile, args):
    projects = load_projects()
    project = next((item for item in projects if profile in item.profiles), None)
    if project is None:
        raise ValueError(f"Profile {profile!r} is not assigned to any project")
    if not os.environ.get("MULTI_AGENT_CLI_SESSION") and legacy_running():
        raise RuntimeError("The legacy Herdr session is still running. Stop it after saving your work before using the new launcher.")
    env = dict(os.environ)
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        env.pop(name, None)
    latest = session_file(project, profile)
    latest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env.update({
        "PATH": os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)]),
        "AGYS_DIR": str(accounts_root()),
        "AGYS_REAL_HOME": str(linux_home),
        "AGYS_NO_AUTO_UPDATE": "1",
        "MULTI_AGENT_CLI": "1",
        "MULTI_AGENT_CLI_RUNNER": "agy-herdr-projects",
        "MULTI_AGENT_CLI_AGYS": str(library / "agys"),
        "MULTI_AGENT_CLI_SESSION_FILE": str(latest),
    })
    windows_browser = Path("/mnt/c/Windows/explorer.exe")
    if "BROWSER" not in env and windows_browser.is_file():
        env["BROWSER"] = str(windows_browser)
    os.chdir(project.repo)
    executable = str(library / "agys")
    lock_dir = state_dir / "profiles"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lock_dir / f"{project.id}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Project {project.id!r} is already running in the workbench") from error
        os.set_inheritable(lock.fileno(), True)
        os.execve(executable, [executable, "run", profile, "--", *args], env)


def choose_count(current):
    while True:
        entered = input(f"Total projects [{current}] (Enter to keep): ").strip()
        if not entered:
            return current
        try:
            if entered.isascii() and entered.isdecimal() and int(entered) >= current:
                return int(entered)
        except ValueError:
            pass
        print(f"Enter a whole number of at least {current}.")


def main():
    if sys.argv[1:2] == ["run"]:
        if len(sys.argv) < 3:
            raise ValueError("Usage: agy-herdr-projects run PROFILE [-- AGY_ARGS...]")
        args = sys.argv[3:]
        run_profile(sys.argv[2], args[1:] if args[:1] == ["--"] else args)
        return

    parser = argparse.ArgumentParser(description="Open and manage Antigravity CLI projects in Herdr.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--count", type=int, help="total projects to prepare")
    action.add_argument("--choose", action="store_true", help="ask for the total number of projects")
    action.add_argument("--add", metavar="ID", help="register a project with --repo and one or more --profile values")
    action.add_argument("--allow", nargs=2, metavar=("ID", "PROFILE"), help="allow another account for an existing project")
    action.add_argument("--use", nargs=2, metavar=("ID", "PROFILE"), help="switch a closed project to a configured account")
    action.add_argument("--list", action="store_true", help="print the projects without opening any panes")
    action.add_argument("--stop", action="store_true", help="stop the upgraded Herdr server and its panes")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--profile", action="append")
    parser.add_argument("--name")
    parser.add_argument("--prepare", action="store_true", help="configure panes without attaching a client")
    parser.add_argument("--fresh", action="store_true", help="start new conversations in panes created by this call")
    args = parser.parse_args()
    if args.count is not None and args.count < 1:
        parser.error("--count must be positive")
    if args.add and (not args.repo or not args.profile):
        parser.error("--add requires --repo and at least one --profile")
    if not args.add and (args.repo or args.profile or args.name):
        parser.error("--repo, --profile and --name require --add")
    if args.list:
        for project in load_projects():
            print(f"{project.id}\t{project.repo}\t{', '.join(project.profiles)}")
        return

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / "setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.stop:
            env = server_environment()
            if herdr(env, "status", "server", "--json")["running"]:
                herdr(env, "server", "stop")
            return
        legacy = legacy_running()
        if legacy and not (args.prepare or args.add or args.allow or args.count or args.use):
            print("The legacy Herdr window is still running. Attaching to it; the upgraded session will open after it stops.",
                  file=sys.stderr)
            os.execvpe(herdr_command(legacy_session)[0], herdr_command(legacy_session), client_environment())
        if legacy:
            raise RuntimeError("The legacy Herdr session is still running. Stop it after saving your work before starting the upgraded session.")

        projects = load_projects(args.count or (1 if args.add else None))
        if args.add:
            if not (state_dir / "projects.json").exists() and not (state_dir / "project-count").exists():
                projects = []
            projects.append(Project(args.add, args.name or args.add, args.repo.expanduser().resolve(), tuple(args.profile)))
        if args.allow:
            project_id, profile = args.allow
            project = next((project for project in projects if project.id == project_id), None)
            if project is None:
                raise ValueError(f"Unknown project {project_id!r}")
            if profile not in project.profiles:
                projects = [replace(item, profiles=(*item.profiles, profile)) if item.id == project_id else item
                            for item in projects]
        requested = choose_count(len(projects)) if args.choose else args.count or len(projects)
        number = 1
        ids = {project.id for project in projects}
        repos = {project.repo.resolve() for project in projects}
        profiles = {profile for project in projects for profile in project.profiles}
        while len(projects) < requested:
            project = project_for(number)
            if project.id not in ids and project.repo.resolve() not in repos and project.profile not in profiles:
                projects.append(project)
            number += 1

        validate_projects(projects)
        root = accounts_root()
        env = server_environment()
        if args.use:
            project_id, profile = args.use
            project = next((project for project in projects if project.id == project_id), None)
            if project is None or profile not in project.profiles:
                raise ValueError(f"Profile {profile!r} is not configured for project {project_id!r}")
            if project.profile != profile and herdr(env, "status", "server", "--json")["running"]:
                workspace_id = find_workspace(env)
                if workspace_id:
                    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                    if project_id in match_panes([project], panes, pane_bindings()):
                        raise ValueError(f"Close the pane for {project.name} before switching its account")
            profiles = (profile, *(existing for existing in project.profiles if existing != profile))
            projects = [replace(item, profiles=profiles) if item.id == project_id else item for item in projects]
        prepare_projects(projects, root)
        save_projects(projects)
        profiles_root = state_dir / "profiles-root.json"
        if not profiles_root.exists():
            write_json(profiles_root, str(root))
        workspace_id = sync_panes(env, projects, start_server(env), args.fresh)
        if not args.prepare:
            herdr(env, "workspace", "focus", workspace_id)

    if not args.prepare:
        os.execvpe(herdr_command()[0], herdr_command(), env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, EOFError) as error:
        raise SystemExit(f"Multi Agent CLI: {error}") from error
