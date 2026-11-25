import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
HERDR = os.environ.get("HERDR_TEST_BIN") or shutil.which("herdr")


@unittest.skipUnless(HERDR and os.name == "posix", "requires a Unix Herdr binary")
class HerdrLayoutTest(unittest.TestCase):
    def test_fresh_tab_starts_argv_without_replacing_live_panes(self):
        from herdr_layout import create_project_tab

        with tempfile.TemporaryDirectory(prefix="mac-layout-") as temporary:
            home = Path(temporary)
            config = home / "config"
            environment = {
                **os.environ,
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(config),
                "XDG_STATE_HOME": str(home / "state"),
            }
            for key in tuple(environment):
                if key.startswith("HERDR_"):
                    del environment[key]
            command = [HERDR, "--session", "layout-test"]
            options = {"cwd": home, "env": environment}

            def herdr(*args):
                response = subprocess.run([*command, *args], **options, check=True,
                                          capture_output=True, text=True)
                return json.loads(response.stdout)["result"]

            with subprocess.Popen([*command, "server"], **options) as server:
                try:
                    self.await_value(lambda: self.ready(herdr))
                    original = herdr("workspace", "create", "--cwd", str(home))
                    workspace = original["workspace"]["workspace_id"]
                    existing_pane = original["root_pane"]
                    project_dir = home / "my repo"
                    project_dir.mkdir()
                    launch_log = home / "argv.json"
                    script = ("import json, os, pathlib, sys, time; "
                              "pathlib.Path(sys.argv[1]).write_text(json.dumps("
                              "[os.getcwd(), sys.argv[2], os.getenv('PROJECT_NOTE')])); "
                              "time.sleep(60)")
                    result = create_project_tab(
                        config / "herdr/sessions/layout-test/herdr.sock", workspace, "New Projects",
                        [{"repo": project_dir, "label": "Project A", "env": {"PROJECT_NOTE": "notes with spaces"},
                          "command": [sys.executable, "-c", script, str(launch_log), "literal $HOME; 'word'"]},
                         {"repo": home, "label": "Project B", "command": [shutil.which("sleep"), "60"]}],
                    )
                    self.await_value(launch_log.exists)
                    self.assertEqual(json.loads(launch_log.read_text()),
                                     [str(project_dir), "literal $HOME; 'word'", "notes with spaces"])
                    panes = herdr("pane", "list", "--workspace", workspace)["panes"]
                    original_by_id = next(pane for pane in panes if pane["pane_id"] == existing_pane["pane_id"])
                    self.assertEqual(original_by_id["terminal_id"], existing_pane["terminal_id"])
                    new_tab = result["layout"]["tab_id"]
                    self.assertNotEqual(new_tab, existing_pane["tab_id"])
                    self.assertCountEqual([pane["label"] for pane in panes if pane["tab_id"] == new_tab],
                                          ["Project A", "Project B"])
                finally:
                    server.terminate()
                    server.wait(timeout=8)

    @staticmethod
    def ready(herdr):
        try:
            return herdr("workspace", "list")
        except subprocess.CalledProcessError:
            return None

    def await_value(self, check):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if value := check():
                return value
            time.sleep(0.05)
        self.fail("Herdr did not complete the expected operation")
