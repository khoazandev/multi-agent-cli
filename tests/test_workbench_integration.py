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
        for file in ("agy-herdr-projects.py", "herdr_layout.py", "desktop_api.py"):
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
            " os.environ['AGYS_DIR'], sys.argv[4:], os.environ.get('MULTI_AGENT_CLI_ACCOUNT')]) + '\\n')\n"
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

    def test_one_login_launches_two_repositories_and_keeps_their_histories_separate(self):
        first_repo, second_repo = self.root / "first", self.root / "second"
        self.launch("--add", "first", "--repo", str(first_repo), "--profile", "shared")
        self.wait_for_launches(1)
        profiles = self.root / "account-state" / "profiles"
        state = Path(".gemini/antigravity-cli")
        auth = state / "antigravity-oauth-token"
        source = profiles / "shared"
        (source / state).mkdir(parents=True)
        (source / auth).write_text('{"token":{"refresh_token":"fixture"}}')
        (source / state / "history.jsonl").write_text("first repo only")
        self.launch("--add", "second", "--repo", str(second_repo), "--profile", "shared")
        launches = self.wait_for_launches(2)
        self.assertEqual([entry[1] for entry in launches], [str(first_repo), str(second_repo)])
        self.assertEqual(launches[0][0], "shared")
        self.assertNotEqual(launches[1][0], "shared")
        second = profiles / launches[1][0]
        self.assertEqual(json.loads((second / auth).read_text()), json.loads((source / auth).read_text()))
        self.assertFalse((second / state / "history.jsonl").exists())

        self.launch("--manage")
        self.launch("--manage")
        workspace = next(item["workspace_id"] for item in self.herdr("workspace", "list")["workspaces"]
                         if item["label"] == "Antigravity Projects")
        tabs = self.herdr("tab", "list", "--workspace", workspace)["tabs"]
        self.assertEqual(sum(tab["label"] == "Accounts & Projects" for tab in tabs), 1)
        self.assertEqual(len(self.wait_for_launches(2)), 2)

        self.stop()
        self.launch("--count", "2")
        self.launch("--manage")
        workspace = next(item["workspace_id"] for item in self.herdr("workspace", "list")["workspaces"]
                         if item["label"] == "Antigravity Projects")
        tabs = self.herdr("tab", "list", "--workspace", workspace)["tabs"]
        self.assertEqual(sum(tab["label"] == "Accounts & Projects" for tab in tabs), 1)
        manager = next(tab for tab in tabs if tab["label"] == "Accounts & Projects")
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        pane = next(item for item in panes if item["tab_id"] == manager["tab_id"])
        recorded = json.loads((self.config_path.parent / "manager-terminal.json").read_text())
        self.assertEqual(recorded, pane["terminal_id"])

    def test_desktop_switch_requires_close_confirmation(self):
        self.launch("--count", "2")
        self.wait_for_launches(2)
        command = [str(self.launcher), "desktop", "assign", "project-a", "acc-b"]
        denied = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(denied.returncode, 1)
        self.assertIn("Close the pane", denied.stderr)
        self.assertEqual(len(self.wait_for_launches(2)), 2)

        allowed = subprocess.run([*command, "--close"], env=self.env, cwd=self.root,
                                 capture_output=True, text=True, timeout=30, check=True)
        result = json.loads(allowed.stdout)
        self.assertTrue(all(project["account"] == "acc-b" for project in result["projects"]))
        launches = self.wait_for_launches(3)
        self.assertEqual(launches[-1][1], launches[0][1] if launches[0][0] == "acc-a" else launches[1][1])
        self.assertNotEqual(launches[-1][0], "acc-b")

    def test_shared_directory_is_passed_to_each_agent(self):
        shared = self.root / "shared work"
        shared.mkdir()
        self.launch("--share-dir", str(shared))
        self.launch("--share-dir", str(shared))
        self.assertFalse(self.log.exists())
        self.launch("--count", "2")
        for launch in self.wait_for_launches(2):
            self.assertEqual(launch[4], ["--add-dir", str(shared)])

    def test_close_open_and_remove_single_project_and_propagate_account_env(self):
        self.launch("--count", "2")
        self.wait_for_launches(2)
        workspace = next(item["workspace_id"] for item in self.herdr("workspace", "list")["workspaces"]
                         if item["label"] == "Antigravity Projects")

        self.launch("--close", "project-a")
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        self.assertEqual(len(panes), 1)
        self.assertIn("[project:project-b]", panes[0]["label"])

        self.launch("--open", "project-b")
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        self.assertEqual(len(panes), 1)
        self.assertEqual(len(self.wait_for_launches(2)), 2)

        extra = self.root / "extra-repo"
        extra.mkdir()
        self.launch("--add", "extra", "--repo", str(extra), "--profile", "acc-b")
        launches = self.wait_for_launches(3)
        self.assertTrue(launches[-1][0].startswith("mac-"))
        self.assertEqual(launches[-1][5], "acc-b")
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        self.assertEqual(len(panes), 2)
        self.assertFalse(any("[project:project-a]" in pane["label"] for pane in panes))

        self.launch("--open", "project-a")
        self.assertEqual(len(self.wait_for_launches(4)), 4)
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        self.assertEqual(len(panes), 3)

        app_dir = self.root / "account-state" / "profiles" / "acc-a" / ".gemini" / "antigravity-cli"
        (app_dir / "brain" / "chat-one").mkdir(parents=True)
        (app_dir / "history.jsonl").write_text(
            json.dumps({"conversationId": "chat-one", "workspace": str(self.root / "user" / "projects" / "agy-a"), "display": "Resume target", "timestamp": 5000}) + "\n"
        )
        history_result = subprocess.run([str(self.launcher), "--history", "project-a"], env=self.env, cwd=self.root,
                                        capture_output=True, text=True, timeout=15, check=True)
        self.assertIn("chat-one", history_result.stdout)
        self.assertIn("Resume target", history_result.stdout)

        self.launch("--open", "project-a", "--conversation", "chat-one", "--close-current-pane")
        launches = self.wait_for_launches(5)
        self.assertEqual(launches[-1][0], "acc-a")
        self.assertEqual(launches[-1][4], ["--conversation=chat-one"])

        denied = self.launch("--remove", "extra", expected=1)
        self.assertIn("Close the pane", denied.stderr)
        self.launch("--close", "extra")
        self.launch("--remove", "extra")
        saved = json.loads((self.config_path.parent / "projects.json").read_text())
        self.assertEqual([item["id"] for item in saved], ["project-a", "project-b"])

        self.launch("--close", "project-a")
        self.launch("--close", "project-b")
        panes = self.herdr("pane", "list", "--workspace", workspace)["panes"]
        self.assertEqual(len(panes), 1)
        self.assertEqual(panes[0]["label"], "[Multi Agent CLI] Setup")

    def test_stale_conversation_pointer_falls_back_to_fresh_session(self):
        fake_agys = self.root / "user" / ".local" / "lib" / "multi-agent-cli" / "agys"
        fake_agys.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys, time\n"
            "if '--conversation=missing-id' in sys.argv:\n"
            "    sys.exit(1)\n"
            f"with open({str(self.log)!r}, 'a') as out:\n"
            "    out.write(json.dumps([sys.argv[2], os.getcwd(), os.environ['MULTI_AGENT_CLI'],"
            " os.environ['AGYS_DIR'], sys.argv[4:], os.environ.get('MULTI_AGENT_CLI_ACCOUNT')]) + '\\n')\n"
            "time.sleep(180)\n"
        )
        fake_agys.chmod(0o755)
        self.launch("--count", "1")
        self.wait_for_launches(1)
        pointer = self.config_path.parent / "conversations" / "project-a" / "acc-a"
        pointer.write_text("missing-id\n")
        self.stop()
        self.launch("--count", "1")
        launches = self.wait_for_launches(2)
        self.assertEqual(launches[1][4], [])
        self.assertFalse(pointer.exists())


if __name__ == "__main__":
    unittest.main()
