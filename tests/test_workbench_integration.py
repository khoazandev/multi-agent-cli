import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


repo = Path(__file__).resolve().parents[1]
herdr_bin = os.environ.get("HERDR_TEST_BIN")


@unittest.skipUnless(herdr_bin, "set HERDR_TEST_BIN to a Linux Herdr 0.9.5 executable")
class WorkbenchIntegrationTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="mac-workbench-")
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        home = self.root / "user"
        library = home / ".local" / "lib" / "multi-agent-cli"
        library.mkdir(parents=True)
        self.launcher = home / ".local" / "bin" / "agy-herdr-projects"
        self.launcher.parent.mkdir()
        for file in ("agy-herdr-projects.py", "herdr_layout.py"):
            shutil.copy(repo / "scripts" / file, library)
        (library / "agy-herdr-projects.py").chmod(0o755)
        self.launcher.symlink_to(library / "agy-herdr-projects.py")
        (library / "herdr").symlink_to(Path(herdr_bin).resolve())
        self.log = self.root / "launches.jsonl"
        fake_agys = library / "agys"
        fake_agys.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys, time\n"
            f"with open({str(self.log)!r}, 'a') as out:\n"
            "    out.write(json.dumps([sys.argv[2], os.getcwd(), os.environ['MULTI_AGENT_CLI'],"
            " os.environ['AGYS_DIR'], sys.argv[4:]]) + '\\n')\n"
            "time.sleep(180)\n"
        )
        fake_agys.chmod(0o755)
        self.config_path = home / ".local" / "state" / "agy-herdr-projects" / "config"
        fingerprint = hashlib.sha256(os.fsencode(self.config_path.parent.resolve())).hexdigest()[:16]
        socket_base = Path(f"/run/user/{os.getuid()}")
        if not socket_base.is_dir():
            socket_base = Path("/tmp")
        self.socket_dir = socket_base / f"multi-agent-cli-{os.getuid()}-{fingerprint}"
        self.env = {
            **os.environ, "AGYS_REAL_HOME": str(home),
            "AGYS_DIR": str(self.root / "account-state"),
            "XDG_CONFIG_HOME": str(self.root / "legacy-config"),
        }
        for key in ("HERDR_CONFIG_PATH", "HERDR_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_SESSION"):
            self.env.pop(key, None)
        self.addCleanup(self.remove_runtime_directory)
        self.addCleanup(self.stop)

    def remove_runtime_directory(self):
        if self.socket_dir.is_dir() and not any(self.socket_dir.iterdir()):
            self.socket_dir.rmdir()

    def stop(self):
        self.herdr("server", "stop", check=False)
        for _ in range(100):
            if not (self.socket_dir / "h.sock").exists():
                break
            time.sleep(0.05)

    def herdr(self, *args, check=True):
        env = {**self.env, "XDG_CONFIG_HOME": str(self.config_path), "HERDR_SOCKET_PATH": str(self.socket_dir / "h.sock")}
        result = subprocess.run([herdr_bin, *args], env=env,
                                check=check, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout).get("result", {}) if result.returncode == 0 and result.stdout.strip() else {}

    def launch(self, *args, expected=0):
        result = subprocess.run([str(self.launcher), *args, "--prepare"], env=self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def wait_for_launches(self, count):
        for _ in range(100):
            launches = self.log.read_text().splitlines() if self.log.exists() else []
            if len(launches) >= count:
                return [json.loads(line) for line in launches]
            time.sleep(0.05)
        self.fail(f"Expected {count} agent launches, saw {len(launches)}")

    def test_grow_layout_without_restarting_agents_or_taking_scratch_panes(self):
        self.launch("--count", "2")
        workspace = next(item["workspace_id"] for item in self.herdr("workspace", "list")["workspaces"]
                         if item["label"] == "Antigravity Projects")
        first = self.wait_for_launches(2)
        self.assertEqual({entry[0] for entry in first}, {"acc-a", "acc-b"})
        self.assertTrue(all(entry[2:4] == ["1", str(self.root / "account-state")] for entry in first))

        def panes():
            return self.herdr("pane", "list", "--workspace", workspace)["panes"]

        initial_ids = {pane["terminal_id"] for pane in panes()}
        scratch = self.herdr("tab", "create", "--workspace", workspace, "--cwd", first[0][1],
                             "--label", "Scratch")["root_pane"]
        self.launch("--count", "6")
        self.launch("--count", "6")
        self.assertEqual(len(self.wait_for_launches(6)), 6)
        final_ids = {pane["terminal_id"] for pane in panes()}
        self.assertEqual(len(final_ids), 7)
        self.assertLessEqual(initial_ids | {scratch["terminal_id"]}, final_ids)

    def test_cold_start_uses_only_the_selected_accounts_saved_conversation(self):
        self.launch("--count", "1")
        self.wait_for_launches(1)
        pointer = self.config_path.parent / "conversations" / "project-a" / "acc-a"
        pointer.write_text("fixture-id\n")
        self.stop()
        self.launch("--count", "1")
        launches = self.wait_for_launches(2)
        self.assertEqual([item[0] for item in launches], ["acc-a", "acc-a"])
        self.assertEqual(launches[1][4], ["--conversation=fixture-id"])

    def test_switch_uses_an_allowed_account_after_closing_its_pane(self):
        directory = self.root / "existing repository"
        directory.mkdir()
        self.launch("--add", "payments", "--repo", str(directory), "--profile", "first")
        self.wait_for_launches(1)
        self.launch("--allow", "payments", "second")
        result = self.launch("--use", "payments", "another", expected=1)
        self.assertIn("not configured", result.stderr)
        result = self.launch("--use", "payments", "second", expected=1)
        self.assertIn("Close the pane", result.stderr)
        workspace = next(item["workspace_id"] for item in self.herdr("workspace", "list")["workspaces"]
                         if item["label"] == "Antigravity Projects")
        pane = self.herdr("pane", "list", "--workspace", workspace)["panes"][0]
        self.herdr("pane", "close", pane["pane_id"])
        self.launch("--use", "payments", "second")
        launches = self.wait_for_launches(2)
        self.assertEqual([item[:2] for item in launches],
                         [["first", str(directory)], ["second", str(directory)]])


if __name__ == "__main__":
    unittest.main()
