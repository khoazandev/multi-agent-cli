#!/usr/bin/env python3

import argparse
from dataclasses import asdict, dataclass, field, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from herdr_layout import create_project_tab, focus_pane


linux_home = Path(os.environ.get("AGYS_REAL_HOME") or Path.home())
bin_dir = linux_home / ".local" / "bin"
library = linux_home / ".local" / "lib" / "multi-agent-cli"
state_dir = linux_home / ".local" / "state" / "agy-herdr-projects"
shared_skills = linux_home / ".local" / "share" / "agy-shared" / "skills"
legacy_session = "agy-projects"
workspace_label = "Antigravity Projects"
starter_label = "[Multi Agent CLI] Setup"
manager_label = "Accounts & Projects"
panes_per_tab = 4
identifier = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
marker = re.compile(r" \[project:([a-zA-Z0-9_-]+)\]$")
conversation_id = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
credential_paths = (
    ".gemini/antigravity-cli/antigravity-oauth-token",
    ".gemini/antigravity/antigravity-oauth-token",
    ".gemini/jetski-standalone-oauth-token",
    ".gemini/antigravity-cli/jetski-standalone-oauth-token",
    ".gemini/antigravity/jetski-standalone-oauth-token",
    ".gemini/antigravity-ide/antigravity-oauth-token",
    ".gemini/oauth_creds.json",
)


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    repo: Path
    profiles: tuple[str, ...]
    profile_slots: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @property
    def profile(self):
        return self.profiles[0]

    def runtime_profile(self, account):
        return self.profile_slots.get(account, account)

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


def _atomic_write(path, writer):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            writer(out)
            out.flush()
            os.fsync(out.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    _atomic_write(path, lambda out: (json.dump(value, out, indent=2, ensure_ascii=False), out.write("\n")))


def write_text_private(path, content):
    _atomic_write(path, lambda out: out.write(content))


def shared_directories():
    manifest = state_dir / "shared-dirs.json"
    try:
        directories = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(directories, list) or any(
        not isinstance(directory, str) or not Path(directory).is_absolute() for directory in directories
    ):
        raise ValueError(f"Expected a list of absolute paths in {manifest}")
    return directories


def shared_directory_args():
    args = []
    for directory in shared_directories():
        if Path(directory).is_dir():
            args.extend(["--add-dir", directory])
        else:
            print(f"Shared directory is unavailable: {directory}", file=sys.stderr)
    return args


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
                enabled = item.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise ValueError("expected boolean enabled flag")
                projects.append(Project(item["id"], item["name"],
                                        Path(item["repo"]).expanduser(), tuple(item["profiles"]),
                                        item.get("profile_slots", {}), enabled))
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
    ids, repos, slots = set(), set(), set()
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
        if not isinstance(project.profile_slots, dict):
            raise ValueError(f"Invalid account storage for {project.id}")
        if not isinstance(project.enabled, bool):
            raise ValueError(f"Invalid enabled state for {project.id}")
        accounts = set()
        for account in project.profiles:
            if not isinstance(account, str) or not identifier.fullmatch(account) or account.lower() == "auto":
                raise ValueError(f"Invalid account for {project.id}: {account!r}")
            slot = project.runtime_profile(account)
            if (account in accounts or not isinstance(slot, str)
                    or not identifier.fullmatch(slot) or slot.lower() == "auto" or slot in slots):
                raise ValueError(f"Duplicate or invalid profile storage for {project.id}: {account}")
            accounts.add(account)
            slots.add(slot)
        if project.profile_slots.keys() - accounts:
            raise ValueError(f"Unexpected account storage for {project.id}")


def assign_account_slots(project, projects):
    slots = dict(project.profile_slots)
    used = {account for item in projects if item.id != project.id for account in item.profiles}
    for account in project.profiles:
        if account in used and account not in slots:
            if any(item.id == project.id and account in item.profiles for item in projects):
                continue  # Preserve a first project's existing profile and history.
            digest = hashlib.sha256(f"{project.id}\0{account}".encode()).hexdigest()[:24]
            slots[account] = f"mac-{digest}"
    return replace(project, profile_slots=slots)


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
        for account in project.profiles:
            profile = project.runtime_profile(account)
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


def saved_google_login(path):
    # Only read an Antigravity credential file, and skip links or bad files.
    if not path.is_file() or path.resolve() != path:
        return None
    try:
        with path.open("rb") as source:
            content = source.read(65537)
        if len(content) > 65536:
            return None
        login = json.loads(content)
        refresh = login["token"]["refresh_token"]
        return login if isinstance(refresh, str) and refresh else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def profile_login_with_mtime(profile_dir):
    for path in credential_paths:
        candidate = profile_dir / path
        login = saved_google_login(candidate)
        if login is not None:
            return login, candidate.stat().st_mtime
    return None, 0.0


def reuse_account_login(project, account, projects, root):
    """Seed only the OAuth credential for a new slot using the same account alias."""
    profiles = root.resolve() / "profiles"
    target = profiles / project.runtime_profile(account)
    for path in credential_paths:
        if (target / path).is_symlink():
            return False
    primary_credential = credential_paths[0]
    destination = target / primary_credential
    seeded_marker = target / ".workbench-seeded-token"
    if destination.resolve() != destination or seeded_marker.is_symlink():
        return False

    target_login, target_mtime = profile_login_with_mtime(target)
    target_hash = None
    if target_login is not None:
        target_hash = hashlib.sha256(target_login["token"]["refresh_token"].encode()).hexdigest()
        recorded_hash = None
        if seeded_marker.is_file():
            try:
                recorded_hash = seeded_marker.read_text(encoding="utf-8").strip()
            except OSError:
                recorded_hash = None
        if recorded_hash != target_hash:
            return False

    peers = [item for item in projects if item.id != project.id and account in item.profiles]
    peers.sort(key=lambda item: item.runtime_profile(account) != account)
    candidates = []
    seen_dirs = {target}
    for peer in peers:
        source = profiles / peer.runtime_profile(account)
        if source not in seen_dirs:
            seen_dirs.add(source)
            candidates.append(source)
    canonical = profiles / account
    if canonical not in seen_dirs:
        candidates.append(canonical)

    best_login = None
    best_mtime = -1.0
    for source in candidates:
        login, mtime = profile_login_with_mtime(source)
        if login is not None and mtime > best_mtime:
            best_login = login
            best_mtime = mtime
    if best_login is None:
        return False

    source_hash = hashlib.sha256(best_login["token"]["refresh_token"].encode()).hexdigest()
    if target_login is not None and (source_hash == target_hash or best_mtime < target_mtime):
        return False

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(destination, best_login)
    write_text_private(seeded_marker, source_hash + "\n")
    return True


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
    command = [str(bin_dir / "agy-herdr-projects"), "run", project.runtime_profile(project.profile)]
    if not fresh:
        try:
            latest = session_file(project, project.profile).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return command
        if conversation_id.fullmatch(latest):
            command += ["--", f"--conversation={latest}"]
    return command


def client_lock_path():
    return state_dir / "client.lock"


def is_client_attached():
    path = client_lock_path()
    if not path.exists():
        return False
    try:
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock, fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def attach_herdr_client(env):
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = client_lock_path().open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(lock.fileno(), True)
    except BlockingIOError:
        lock.close()
    os.execvpe(herdr_command()[0], herdr_command(), env)


def find_workspace(env):
    return next((item["workspace_id"] for item in herdr(env, "workspace", "list")["workspaces"]
                 if item.get("label") == workspace_label), None)


def focus_project_pane(env, workspace_id, pane):
    herdr(env, "workspace", "focus", workspace_id)
    if pane.get("tab_id"):
        herdr(env, "tab", "focus", pane["tab_id"])
    focus_pane(env["HERDR_SOCKET_PATH"], pane["pane_id"])


def close_project_pane(env, workspace_id, pane_id):
    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
    if len(panes) <= 1 and any(pane["pane_id"] == pane_id for pane in panes):
        created = herdr(env, "tab", "create", "--workspace", workspace_id,
                        "--cwd", str(linux_home), "--label", starter_label, "--no-focus")
        herdr(env, "pane", "rename", created["root_pane"]["pane_id"], starter_label)
    herdr(env, "pane", "close", pane_id)


def sync_panes(env, projects, starting, fresh, target_ids=None):
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

    desired = [project for project in projects if (project.enabled if target_ids is None else project.id in target_ids)]
    missing = [project for project in desired if project.id not in matched]
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


def repo_root(path):
    try:
        current = Path(path).resolve()
    except OSError:
        current = Path(path)
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            try:
                return Path(path).resolve()
            except OSError:
                return Path(path)
        current = current.parent


def clean_conversation_title(text):
    if not isinstance(text, str):
        return ""
    matched = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL)
    if matched:
        text = matched.group(1)
    cleaned = " ".join(text.split())
    if not cleaned or cleaned.startswith("[AGYS_INTERNAL_") or cleaned == "(No prompt summary)":
        return ""
    if len(cleaned) > 90:
        return cleaned[:87].rstrip() + "..."
    return cleaned


def transcript_title(transcript_path):
    try:
        with transcript_path.open("r", encoding="utf-8", errors="ignore") as stream:
            lines = stream.read(65536).splitlines()
    except OSError:
        return ""
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "USER_INPUT" or entry.get("source") == "USER_EXPLICIT":
            title = clean_conversation_title(entry.get("content", ""))
            if title and not title.startswith("/"):
                return title
    return ""


def list_project_conversations(project, account, root, limit=20):
    profiles_dir = root.resolve() / "profiles" / project.runtime_profile(account)
    expected_root = repo_root(project.repo)
    pointer = session_file(project, account)
    current_id = ""
    try:
        candidate = pointer.read_text(encoding="utf-8").strip()
        if conversation_id.fullmatch(candidate):
            current_id = candidate
    except OSError:
        pass

    history_entries = {}
    for app in ("antigravity-cli", "antigravity"):
        history_path = profiles_dir / ".gemini" / app / "history.jsonl"
        if not history_path.is_file():
            continue
        try:
            lines = history_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            if "conversationId" not in line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            conv_id = item.get("conversationId")
            if not isinstance(conv_id, str) or not conversation_id.fullmatch(conv_id):
                continue
            raw_display = item.get("display", "")
            if isinstance(raw_display, str) and raw_display.startswith("[AGYS_INTERNAL_"):
                continue
            title = clean_conversation_title(raw_display)
            workspace = item.get("workspace") or ""
            timestamp = item.get("timestamp") or 0
            if not isinstance(timestamp, (int, float)):
                timestamp = 0
            existing = history_entries.get(conv_id)
            if existing is None:
                history_entries[conv_id] = {
                    "workspace": workspace,
                    "title": title,
                    "timestamp": float(timestamp),
                }
            else:
                if not existing["workspace"] and workspace:
                    existing["workspace"] = workspace
                if (not existing["title"] or existing["title"].startswith("/")) and title and not title.startswith("/"):
                    existing["title"] = title
                if timestamp > existing["timestamp"]:
                    existing["timestamp"] = float(timestamp)

    if current_id and current_id not in history_entries:
        history_entries[current_id] = {"workspace": "", "title": "", "timestamp": 0.0}

    results = []
    for conv_id, info in history_entries.items():
        workspace = info["workspace"]
        if workspace and repo_root(Path(workspace)) != expected_root:
            continue
        mtime = info["timestamp"] / 1000.0 if info["timestamp"] > 0 else 0.0
        artifact = None
        fallback_title = ""
        for app in ("antigravity-cli", "antigravity"):
            app_dir = profiles_dir / ".gemini" / app
            brain = app_dir / "brain" / conv_id
            transcript = brain / ".system_generated" / "logs" / "transcript.jsonl"
            db = app_dir / "conversations" / f"{conv_id}.db"
            for candidate in (transcript, brain, db):
                try:
                    if candidate.exists() and not candidate.is_symlink():
                        mtime = max(mtime, candidate.stat().st_mtime)
                        if artifact is None:
                            artifact = candidate
                except OSError:
                    continue
            if not fallback_title and transcript.is_file() and not transcript.is_symlink():
                fallback_title = transcript_title(transcript)
        if artifact is None:
            continue
        title = info["title"] or fallback_title or "(Phiên chưa đặt tên)"
        updated_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime > 0 else ""
        results.append((
            mtime,
            conv_id,
            {
                "id": conv_id,
                "title": title,
                "updated_at": updated_at,
                "path": str(artifact),
                "current": conv_id == current_id,
            },
        ))

    results.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in results[:limit]]


def has_resumable_conversation(profile_root, profile, conv_id, project_repo):
    profiles_dir = profile_root / "profiles" / profile
    has_files = False
    for app in ("antigravity-cli", "antigravity"):
        app_dir = profiles_dir / ".gemini" / app
        if (app_dir / "brain" / conv_id).is_dir() or (app_dir / "conversations" / f"{conv_id}.db").is_file():
            has_files = True
            break
    if not has_files:
        return False
    expected_root = repo_root(project_repo)
    for app in ("antigravity-cli", "antigravity"):
        history = profiles_dir / ".gemini" / app / "history.jsonl"
        if not history.is_file():
            continue
        try:
            for line in history.read_text(encoding="utf-8", errors="ignore").splitlines():
                if conv_id not in line:
                    continue
                entry = json.loads(line)
                if entry.get("conversationId") != conv_id:
                    continue
                workspace = entry.get("workspace")
                if not workspace or repo_root(Path(workspace)) == expected_root:
                    return True
        except (OSError, ValueError):
            continue
    return False


def run_profile(profile, args):
    projects = load_projects()
    match = next(((item, account) for item in projects for account in item.profiles
                  if item.runtime_profile(account) == profile), None)
    if match is None:
        raise ValueError(f"Profile {profile!r} is not assigned to any project")
    project, account = match
    if not os.environ.get("MULTI_AGENT_CLI_SESSION") and legacy_running():
        raise RuntimeError("The legacy Herdr session is still running. Stop it after saving your work before using the new launcher.")
    env = dict(os.environ)
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        env.pop(name, None)
    latest = session_file(project, account)
    latest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile_root = accounts_root()
    env.update({
        "PATH": os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)]),
        "AGYS_DIR": str(profile_root),
        "AGYS_REAL_HOME": str(linux_home),
        "AGYS_NO_AUTO_UPDATE": "1",
        "MULTI_AGENT_CLI": "1",
        "MULTI_AGENT_CLI_ACCOUNT": account,
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
    try:
        saved_id = latest.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        saved_id = ""
    resume_flag = f"--conversation={saved_id}" if saved_id else ""
    resuming_saved = bool(resume_flag and resume_flag in args)

    with (lock_dir / f"{project.id}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Project {project.id!r} is already running in the workbench") from error
        reuse_account_login(project, account, projects, profile_root)
        os.set_inheritable(lock.fileno(), True)
        if resuming_saved:
            started = time.monotonic()
            completed = subprocess.run(
                [executable, "run", profile, "--", *shared_directory_args(), *args],
                env=env,
                check=False,
            )
            if (
                completed.returncode != 0
                and (time.monotonic() - started) < 3.0
                and not has_resumable_conversation(profile_root, profile, saved_id, project.repo)
            ):
                latest.unlink(missing_ok=True)
                print(
                    "[Multi Agent CLI] Phiên hội thoại cũ không còn khả dụng, đang mở phiên mới...",
                    file=sys.stderr,
                )
                fresh_args = [item for item in args if item != resume_flag]
                os.execve(executable, [executable, "run", profile, "--", *shared_directory_args(), *fresh_args], env)
            raise SystemExit(completed.returncode)
        os.execve(executable, [executable, "run", profile, "--", *shared_directory_args(), *args], env)


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


def open_manager(env, workspace_id):
    tabs = herdr(env, "tab", "list", "--workspace", workspace_id)["tabs"]
    possible_tabs = {tab["tab_id"] for tab in tabs if tab.get("label") == manager_label}
    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
    existing = next((pane for pane in panes if pane.get("label") == manager_label
                     and pane["tab_id"] in possible_tabs), None)
    saved = state_dir / "manager-terminal.json"
    if existing and saved.exists():
        try:
            if json.loads(saved.read_text(encoding="utf-8")) == existing["terminal_id"]:
                return existing["tab_id"]
        except ValueError:
            pass
    result = create_project_tab(env["HERDR_SOCKET_PATH"], workspace_id, manager_label, [{
        "repo": linux_home,
        "label": manager_label,
        "command": [str(bin_dir / "agy-herdr-projects"), "manage"],
    }])
    if existing:
        herdr(env, "pane", "close", existing["pane_id"])
    tab_id = result["layout"]["tab_id"]
    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
    pane = next((pane for pane in panes if pane["tab_id"] == tab_id and pane.get("label") == manager_label), None)
    if pane is None:
        raise RuntimeError("The accounts and projects pane did not start")
    write_json(saved, pane["terminal_id"])
    return tab_id


def manage_projects():
    launcher = str(bin_dir / "agy-herdr-projects")
    while True:
        projects = load_projects()
        print("\nMulti Agent CLI · Tài khoản và dự án")
        for account in sorted({account for project in projects for account in project.profiles}):
            print(f"\n{account}")
            for project in projects:
                if account in project.profiles:
                    active = " · đang chọn" if project.profile == account else ""
                    state = "" if project.enabled else " · đang đóng"
                    print(f"  {project.id}  {project.name}{active}{state}\n    {project.repo}")
        print("\n1  Thêm repo    2  Gán tài khoản    3  Đổi tài khoản    4  Mở pane    5  Đóng pane    6  Gỡ dự án    7  Lịch sử chat    q  Đóng tab")
        choice = input("> ").strip().lower()
        if choice == "q":
            return
        if choice == "1":
            project_id = input("Mã dự án > ").strip()
            repo = input("Thư mục repo trong WSL > ").strip()
            account = input("Tài khoản mới hoặc đã có > ").strip()
            if not all((project_id, repo, account)):
                continue
            args = ["--add", project_id, "--repo", repo, "--profile", account]
        elif choice in ("2", "3"):
            project_id = input("Mã dự án > ").strip()
            account = input("Tài khoản > ").strip()
            args = ["--allow" if choice == "2" else "--use", project_id, account]
        elif choice in ("4", "5", "6"):
            project_id = input("Mã dự án > ").strip()
            if not any(project.id == project_id for project in projects):
                print("Không tìm thấy dự án.")
                continue
            if choice == "4":
                args = ["--open", project_id]
            elif choice == "5":
                args = ["--close", project_id]
            else:
                confirm = input(f"Gỡ dự án {project_id}? (y/N) > ").strip().lower()
                if confirm not in ("y", "yes"):
                    continue
                args = ["--remove", project_id, "--close-current-pane"]
        elif choice == "7":
            project_id = input("Mã dự án > ").strip()
            project = next((item for item in projects if item.id == project_id), None)
            if project is None:
                print("Không tìm thấy dự án.")
                continue
            root = accounts_root()
            storage = root.resolve() / "profiles" / project.runtime_profile(project.profile) / ".gemini" / "antigravity-cli"
            sessions = list_project_conversations(project, project.profile, root)
            print(f"\nThư mục lưu: {storage}")
            if not sessions:
                print("Chưa có đoạn chat nào được lưu cho dự án và tài khoản này.")
            for index, session in enumerate(sessions, start=1):
                tag = " [đang chọn]" if session["current"] else ""
                print(f"  {index}. {session['updated_at']}  {session['title']}{tag}\n     ID: {session['id']}")
            picked = input("\nChọn số để mở lại, 'n' để mở đoạn chat mới, hoặc Enter để quay lại > ").strip().lower()
            if not picked:
                continue
            if picked == "n":
                args = ["--open", project_id, "--fresh", "--close-current-pane"]
            elif picked.isdecimal() and 1 <= int(picked) <= len(sessions):
                chosen = sessions[int(picked) - 1]
                args = ["--open", project_id, "--conversation", chosen["id"], "--close-current-pane"]
            else:
                continue
        else:
            continue
        # Reuse the normal CLI so the same configuration lock, validation, and pane lifecycle apply.
        subprocess.run([launcher, *args, "--prepare"], check=False)


def main():
    if sys.argv[1:2] == ["desktop"]:
        from desktop_api import desktop_main
        desktop_main(sys.modules[__name__], sys.argv[2:])
        return
    if sys.argv[1:2] == ["manage"]:
        manage_projects()
        return
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
    action.add_argument("--manage", action="store_true", help="open the accounts and projects tab")
    action.add_argument("--add", metavar="ID", help="register a project with --repo and one or more --profile values")
    action.add_argument("--allow", nargs=2, metavar=("ID", "PROFILE"), help="allow another account for an existing project")
    action.add_argument("--use", nargs=2, metavar=("ID", "PROFILE"), help="switch a closed project to a configured account")
    action.add_argument("--assign", nargs=2, metavar=("ID", "PROFILE"), help="assign and open a project with an account")
    action.add_argument("--open", metavar="ID", help="open and focus a single project pane")
    action.add_argument("--close", metavar="ID", help="close a project pane without removing the project")
    action.add_argument("--remove", metavar="ID", help="remove a project from the workbench")
    action.add_argument("--history", metavar="ID", help="list saved conversations for a project")
    action.add_argument("--attach", action="store_true", help="attach to the Herdr client without reopening closed projects")
    action.add_argument("--list", action="store_true", help="print the projects without opening any panes")
    action.add_argument("--stop", action="store_true", help="stop the upgraded Herdr server and its panes")
    action.add_argument("--share-dir", type=Path, metavar="PATH", help="add a workspace folder to future CLI sessions")
    action.add_argument("--unshare-dir", type=Path, metavar="PATH", help="remove a shared workspace folder")
    action.add_argument("--shared-dirs", action="store_true", help="list shared workspace folders")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--profile", action="append")
    parser.add_argument("--name")
    parser.add_argument("--conversation", metavar="CONV_ID", help="open a specific saved conversation with --open")
    parser.add_argument("--prepare", action="store_true", help="configure panes without attaching a client")
    parser.add_argument("--fresh", action="store_true", help="start new conversations in panes created by this call")
    parser.add_argument("--close-current-pane", action="store_true", help="allow --assign, --remove, or --open to end the current project CLI")
    args = parser.parse_args()
    if args.count is not None and args.count < 1:
        parser.error("--count must be positive")
    if args.add and (not args.repo or not args.profile):
        parser.error("--add requires --repo and at least one --profile")
    if not args.add and (args.repo or args.profile or args.name):
        parser.error("--repo, --profile and --name require --add")
    if args.conversation is not None:
        if not args.open:
            parser.error("--conversation requires --open")
        if args.fresh:
            parser.error("--conversation cannot be used with --fresh")
        if not conversation_id.fullmatch(args.conversation):
            parser.error("--conversation must be a valid conversation ID")
    if args.close_current_pane and not (args.assign or args.remove or args.open):
        parser.error("--close-current-pane requires --assign, --remove, or --open")
    if args.list:
        for project in load_projects():
            print(f"{project.id}\t{project.repo}\t{', '.join(project.profiles)}")
        return
    if args.shared_dirs:
        print("\n".join(shared_directories()))
        return

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / "setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.share_dir or args.unshare_dir:
            directory = (args.share_dir or args.unshare_dir).expanduser().resolve()
            directories = shared_directories()
            if args.share_dir:
                if not directory.is_dir():
                    raise ValueError(f"Directory is unavailable: {directory}")
                if str(directory) not in directories:
                    directories.append(str(directory))
            else:
                directories = [item for item in directories if item != str(directory)]
            write_json(state_dir / "shared-dirs.json", directories)
            return
        if args.stop:
            env = server_environment()
            if herdr(env, "status", "server", "--json")["running"]:
                herdr(env, "server", "stop")
            return
        legacy = legacy_running()
        if legacy and not (args.prepare or args.add or args.allow or args.count or args.use or args.assign or args.open or args.close or args.remove or args.history):
            print("The legacy Herdr window is still running. Attaching to it; the upgraded session will open after it stops.",
                  file=sys.stderr)
            os.execvpe(herdr_command(legacy_session)[0], herdr_command(legacy_session), client_environment())
        if legacy:
            raise RuntimeError("The legacy Herdr session is still running. Stop it after saving your work before starting the upgraded session.")

        if args.history:
            projects = load_projects()
            project = next((item for item in projects if item.id == args.history), None)
            if project is None:
                raise ValueError(f"Unknown project {args.history!r}")
            root = accounts_root()
            storage = root.resolve() / "profiles" / project.runtime_profile(project.profile) / ".gemini" / "antigravity-cli"
            sessions = list_project_conversations(project, project.profile, root)
            print(f"Project: {project.id} ({project.profile})")
            print(f"Storage: {storage}")
            if not sessions:
                print("No saved conversations found.")
                return
            for session in sessions:
                mark = "*" if session["current"] else " "
                print(f"{mark} {session['updated_at']}\t{session['id']}\t{session['title']}\t{session['path']}")
            return

        if args.close:
            projects = load_projects()
            if not any(project.id == args.close for project in projects):
                raise ValueError(f"Unknown project {args.close!r}")
            projects = [replace(item, enabled=False) if item.id == args.close else item for item in projects]
            validate_projects(projects)
            save_projects(projects)
            env = server_environment()
            if herdr(env, "status", "server", "--json")["running"]:
                workspace_id = find_workspace(env)
                if workspace_id:
                    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                    pane = match_panes(projects, panes, pane_bindings()).get(args.close)
                    if pane:
                        close_project_pane(env, workspace_id, pane["pane_id"])
                    final = match_panes(projects, herdr(env, "pane", "list", "--workspace", workspace_id)["panes"], pane_bindings())
                    write_json(state_dir / "panes.json", {p: item["terminal_id"] for p, item in final.items()})
            return

        if args.remove:
            projects = load_projects()
            project = next((item for item in projects if item.id == args.remove), None)
            if project is None:
                raise ValueError(f"Unknown project {args.remove!r}")
            env = server_environment()
            workspace_id = None
            if herdr(env, "status", "server", "--json")["running"]:
                workspace_id = find_workspace(env)
                if workspace_id:
                    panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                    pane = match_panes([project], panes, pane_bindings()).get(args.remove)
                    if pane:
                        if not args.close_current_pane:
                            raise ValueError(f"Close the pane for {project.name} before removing it")
                        close_project_pane(env, workspace_id, pane["pane_id"])
            projects = [item for item in projects if item.id != args.remove]
            validate_projects(projects)
            save_projects(projects)
            shutil.rmtree(state_dir / "conversations" / args.remove, ignore_errors=True)
            if workspace_id:
                final = match_panes(projects, herdr(env, "pane", "list", "--workspace", workspace_id)["panes"], pane_bindings())
                write_json(state_dir / "panes.json", {p: item["terminal_id"] for p, item in final.items()})
            return

        if args.attach:
            projects = load_projects()
            validate_projects(projects)
            root = accounts_root()
            env = server_environment()
            prepare_projects(projects, root)
            profiles_root = state_dir / "profiles-root.json"
            if not profiles_root.exists():
                write_json(profiles_root, str(root))
            starting = start_server(env)
            workspace_id = find_workspace(env)
            if starting or workspace_id is None:
                workspace_id = sync_panes(env, projects, starting, args.fresh, target_ids=None)
            if not args.prepare:
                herdr(env, "workspace", "focus", workspace_id)
        else:
            projects = load_projects(args.count or (1 if args.add else None))
            target_ids = None
            if args.add:
                if not (state_dir / "projects.json").exists() and not (state_dir / "project-count").exists():
                    projects = []
                project = Project(args.add, args.name or args.add, args.repo.expanduser().resolve(), tuple(args.profile))
                projects.append(assign_account_slots(project, projects))
                target_ids = {args.add}
            if args.allow or args.assign:
                project_id, profile = args.allow or args.assign
                project = next((project for project in projects if project.id == project_id), None)
                if project is None:
                    raise ValueError(f"Unknown project {project_id!r}")
                if profile not in project.profiles:
                    updated = assign_account_slots(replace(project, profiles=(*project.profiles, profile)), projects)
                    projects = [updated if item.id == project_id else item for item in projects]
                if args.allow:
                    target_ids = set()
            if args.open:
                if not any(project.id == args.open for project in projects):
                    raise ValueError(f"Unknown project {args.open!r}")
                projects = [replace(item, enabled=True) if item.id == args.open else item for item in projects]
                target_ids = {args.open}
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
            pane_to_close = None
            if args.use or args.assign:
                project_id, profile = args.use or args.assign
                project = next((project for project in projects if project.id == project_id), None)
                if project is None or profile not in project.profiles:
                    raise ValueError(f"Profile {profile!r} is not configured for project {project_id!r}")
                if project.profile != profile and herdr(env, "status", "server", "--json")["running"]:
                    workspace_id = find_workspace(env)
                    if workspace_id:
                        panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                        pane = match_panes([project], panes, pane_bindings()).get(project_id)
                        if pane:
                            if not args.close_current_pane:
                                raise ValueError(f"Close the pane for {project.name} before switching its account")
                            pane_to_close = pane["pane_id"]
                profiles = (profile, *(existing for existing in project.profiles if existing != profile))
                projects = [replace(item, profiles=profiles, enabled=True) if item.id == project_id else item for item in projects]
                target_ids = {project_id}
            prepare_projects(projects, root)
            if args.open:
                open_project = next(item for item in projects if item.id == args.open)
                pointer = session_file(open_project, open_project.profile)
                pointer.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if args.conversation:
                    conversations = list_project_conversations(open_project, open_project.profile, root, limit=500)
                    if not any(item["id"] == args.conversation for item in conversations) and not has_resumable_conversation(
                        root, open_project.runtime_profile(open_project.profile), args.conversation, open_project.repo
                    ):
                        raise ValueError("Không tìm thấy đoạn chat đã chọn trong dự án này.")
                    write_text_private(pointer, args.conversation + "\n")
                elif args.fresh:
                    pointer.unlink(missing_ok=True)
                if (args.conversation or args.fresh) and herdr(env, "status", "server", "--json")["running"]:
                    workspace_id = find_workspace(env)
                    if workspace_id:
                        panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                        existing_pane = match_panes([open_project], panes, pane_bindings()).get(args.open)
                        if existing_pane:
                            if not args.close_current_pane:
                                raise ValueError(f"Close the pane for {open_project.name} before switching its conversation")
                            close_project_pane(env, workspace_id, existing_pane["pane_id"])
            if pane_to_close:
                close_project_pane(env, workspace_id, pane_to_close)
            save_projects(projects)
            profiles_root = state_dir / "profiles-root.json"
            if not profiles_root.exists():
                write_json(profiles_root, str(root))
            if args.allow and args.prepare and not herdr(env, "status", "server", "--json")["running"]:
                return
            starting = start_server(env)
            workspace_id = sync_panes(env, projects, starting, args.fresh, target_ids=target_ids)
            if args.open:
                panes = herdr(env, "pane", "list", "--workspace", workspace_id)["panes"]
                pane = match_panes(projects, panes, pane_bindings()).get(args.open)
                if pane:
                    focus_project_pane(env, workspace_id, pane)
            manager_id = open_manager(env, workspace_id) if args.manage else None
            if not args.prepare:
                herdr(env, "workspace", "focus", workspace_id)
                if manager_id:
                    herdr(env, "tab", "focus", manager_id)

    if not args.prepare:
        attach_herdr_client(env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, EOFError) as error:
        raise SystemExit(f"Multi Agent CLI: {error}") from error
