import json
import socket


def create_project_tab(socket_path, workspace_id, tab_label, panes):
    """Append a tab whose panes start argv directly, with up to four projects."""
    if not 1 <= len(panes) <= 4:
        raise ValueError("A project tab requires one to four panes")

    leaves = [
        {
            "type": "pane",
            "label": pane["label"],
            "cwd": str(pane["repo"]),
            "command": pane["command"],
            "env": pane.get("env", {}),
        }
        for pane in panes
    ]
    left = leaves[0]
    if len(leaves) == 4:
        left = _split("down", left, leaves[3])
    root = left
    if len(leaves) >= 2:
        right = leaves[1]
        if len(leaves) >= 3:
            right = _split("down", right, leaves[2])
        root = _split("right", left, right)

    result = _rpc(
        socket_path,
        "multi-agent-cli:layout-apply",
        "layout.apply",
        {"workspace_id": workspace_id, "tab_label": tab_label, "root": root},
    )
    return result


def focus_pane(socket_path, pane_id):
    """Focus a specific pane by ID via Herdr's UNIX socket JSON-RPC."""
    return _rpc(socket_path, "multi-agent-cli:pane-focus", "pane.focus", {"pane_id": pane_id})


def _rpc(socket_path, request_id, method, params):
    request = {"id": request_id, "method": method, "params": params}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with connection.makefile("rb") as stream:
            line = stream.readline()
    if not line:
        raise RuntimeError(f"Herdr closed the connection during {method}")
    response = json.loads(line)
    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"Herdr {method} failed ({error['code']}): {error['message']}")
    return response["result"]


def _split(direction, first, second):
    return {"type": "split", "direction": direction, "ratio": 0.5, "first": first, "second": second}
