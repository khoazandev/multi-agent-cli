"""Local JSON interface for the Rust desktop manager."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def convert_repo(value):
    path = value.strip().replace("\\", "/")
    if path.startswith("//?/UNC/"):
        path = "//" + path[8:]
    elif path.startswith("//?/"):
        path = path[4:]
    if path.startswith("//"):
        parts = path[2:].split("/", 2)
        if len(parts) != 3 or parts[0].lower() not in ("wsl.localhost", "wsl$") or parts[1].lower() != "ubuntu":
            raise ValueError("Chọn thư mục Windows hoặc thư mục thuộc Ubuntu WSL.")
        path = "/" + parts[2]
    elif re.match(r"^[a-zA-Z]:/", path):
        result = subprocess.run(["wslpath", "-a", "-u", path], text=True, capture_output=True,
                                timeout=10, check=False)
        if result.returncode:
            raise ValueError("Không đọc được đường dẫn Windows này từ Ubuntu WSL.")
        path = result.stdout.strip()
    result = Path(path).expanduser()
    if not result.is_absolute():
        raise ValueError("Chọn một đường dẫn thư mục đầy đủ.")
    return result.resolve()


def validate_account(workbench, account):
    if not isinstance(account, str) or not workbench.identifier.fullmatch(account) or account.lower() == "auto":
        raise ValueError("Tên tài khoản chỉ gồm chữ, số, dấu gạch ngang (-) hoặc gạch dưới (_), ví dụ: acc-a.")


def snapshot(workbench):
    projects = workbench.load_projects()
    accounts_dir = workbench.accounts_root()
    root = accounts_dir.resolve() / "profiles"
    env = workbench.server_environment()
    running = workbench.herdr(env, "status", "server", "--json")["running"]
    workspace = workbench.find_workspace(env) if running else None
    panes = workbench.herdr(env, "pane", "list", "--workspace", workspace)["panes"] if workspace else []
    matched = workbench.match_panes(projects, panes, workbench.pane_bindings())
    states = {}
    for project in projects:
        for account in project.profiles:
            candidates = (root / project.runtime_profile(account), root / account)
            if not states.get(account):
                states[account] = any(
                    workbench.saved_google_login(slot / path) is not None
                    for slot in candidates
                    for path in workbench.credential_paths
                )
    return {
        "accounts": [{"name": account, "signed_in": signed_in} for account, signed_in in sorted(states.items())],
        "projects": [{
            "id": item.id,
            "name": item.name,
            "repo": str(item.repo),
            "account": item.profile,
            "accounts": item.profiles,
            "running": item.id in matched,
            "storage": str(root / item.runtime_profile(item.profile) / ".gemini" / "antigravity-cli"),
            "sessions": workbench.list_project_conversations(item, item.profile, accounts_dir, limit=15),
        } for item in projects],
        "server_running": running,
        "client_attached": workbench.is_client_attached(),
    }


def launch(workbench, *arguments):
    result = subprocess.run([str(workbench.bin_dir / "agy-herdr-projects"), *arguments, "--prepare"],
                            capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Không cập nhật được phiên CLI.")


def desktop_main(workbench, arguments):
    parser = argparse.ArgumentParser(prog="agy-herdr-projects desktop")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    add = actions.add_parser("add")
    add.add_argument("name")
    add.add_argument("repo")
    add.add_argument("account")
    assign = actions.add_parser("assign")
    assign.add_argument("project")
    assign.add_argument("account")
    assign.add_argument("--close", action="store_true")
    focus = actions.add_parser("focus")
    focus.add_argument("project")
    focus.add_argument("--conversation", default=None)
    focus.add_argument("--fresh", action="store_true")
    focus.add_argument("--close", action="store_true")
    close = actions.add_parser("close")
    close.add_argument("project")
    remove = actions.add_parser("remove")
    remove.add_argument("project")
    remove.add_argument("--close", action="store_true")
    args = parser.parse_args(arguments)

    if args.action == "add":
        validate_account(workbench, args.account)
        repo = convert_repo(args.repo)
        if not repo.is_dir():
            raise ValueError("Thư mục repo không tồn tại hoặc Ubuntu WSL không đọc được.")
        projects = workbench.load_projects()
        used = {project.id for project in projects}
        slug = re.sub(r"[^a-z0-9]+", "-", repo.name.lower()).strip("-")[:48] or "project"
        project_id = slug
        index = 2
        while project_id in used:
            project_id = f"{slug}-{index}"
            index += 1
        launch(workbench, "--add", project_id, "--name", args.name, "--repo", str(repo), "--profile", args.account)
    elif args.action == "assign":
        validate_account(workbench, args.account)
        close_flag = ["--close-current-pane"] if args.close else []
        launch(workbench, "--assign", args.project, args.account, *close_flag)
    elif args.action == "focus":
        if args.project not in {project.id for project in workbench.load_projects()}:
            raise ValueError("Không tìm thấy dự án đã chọn.")
        extra = []
        if args.conversation:
            extra.extend(["--conversation", args.conversation])
        if args.fresh:
            extra.append("--fresh")
        if args.close:
            extra.append("--close-current-pane")
        launch(workbench, "--open", args.project, *extra)
        env = workbench.server_environment()
        workspace = workbench.find_workspace(env)
        panes = workbench.herdr(env, "pane", "list", "--workspace", workspace)["panes"]
        pane = workbench.match_panes(workbench.load_projects(), panes, workbench.pane_bindings()).get(args.project)
        if pane is None:
            raise RuntimeError("Pane chưa khởi động. Kiểm tra repo hoặc đăng nhập lại.")
        workbench.focus_project_pane(env, workspace, pane)
    elif args.action == "close":
        if args.project not in {project.id for project in workbench.load_projects()}:
            raise ValueError("Không tìm thấy dự án đã chọn.")
        launch(workbench, "--close", args.project)
    elif args.action == "remove":
        if args.project not in {project.id for project in workbench.load_projects()}:
            raise ValueError("Không tìm thấy dự án đã chọn.")
        close_flag = ["--close-current-pane"] if args.close else []
        launch(workbench, "--remove", args.project, *close_flag)
    print(json.dumps(snapshot(workbench), ensure_ascii=False))
