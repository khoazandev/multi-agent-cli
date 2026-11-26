import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("workbench", scripts / "agy-herdr-projects.py")
workbench = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = workbench
spec.loader.exec_module(workbench)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.profiles = self.root / "custom-profiles-root"
        self.stack = [
            patch.object(workbench, "linux_home", self.root),
            patch.object(workbench, "state_dir", self.state),
            patch.object(workbench, "shared_skills", self.root / "skills"),
        ]
        for setting in self.stack:
            setting.start()
            self.addCleanup(setting.stop)

    def test_explicit_fresh_count_and_legacy_migration(self):
        self.assertEqual(len(workbench.load_projects(2)), 2)
        (self.state / "project-count").write_text("6\n")
        self.assertEqual(len(workbench.load_projects(2)), 6)
        (self.state / "project-count").write_text("corrupt\n")
        self.assertEqual(len(workbench.load_projects(1)), 1)

    def test_pane_ids_survive_cwd_changes_and_ignore_scratch_pane(self):
        project = workbench.project_for(1)
        managed = {"pane_id": "w1:p1", "terminal_id": "term-original",
                   "label": project.pane_label, "cwd": str(project.repo / "sub")}
        scratch = {"pane_id": "w1:p2", "terminal_id": "term-scratch", "cwd": str(project.repo)}
        result = workbench.match_panes([project], [scratch, managed], {})
        self.assertEqual(result[project.id]["pane_id"], "w1:p1")
        managed["label"] = "Renamed"
        result = workbench.match_panes([project], [scratch, managed], {project.id: "term-original"})
        self.assertEqual(result[project.id]["pane_id"], "w1:p1")

    def test_existing_skill_manifest_keeps_user_content(self):
        project = workbench.project_for(1)
        manifest = self.profiles / "profiles" / project.profile / ".gemini" / "config" / "skills.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"entries":[{"path":"/user/skills"}],"exclude":["old"]}')
        workbench.prepare_projects([project], self.profiles)
        workbench.prepare_projects([project], self.profiles)
        result = json.loads(manifest.read_text())
        self.assertEqual(result["exclude"], ["old"])
        self.assertEqual(result["entries"], [{"path": "/user/skills"}, {"path": str(self.root / "skills")}])

    def test_two_projects_share_account_name_with_distinct_profile_storage(self):
        first = workbench.project_for(1)
        second = workbench.Project("other", "Other project", self.root / "another", (first.profile,))
        second = workbench.assign_account_slots(second, [first])
        workbench.validate_projects([first, second])
        workbench.save_projects([first, second])
        first, second = workbench.load_projects()
        self.assertEqual(first.profile, second.profile)
        self.assertEqual(first.runtime_profile(first.profile), first.profile)
        self.assertNotEqual(first.runtime_profile(first.profile), second.runtime_profile(second.profile))
        workbench.prepare_projects([first, second], self.profiles)
        self.assertTrue((self.profiles / "profiles" / second.runtime_profile(second.profile)).is_dir())

    def test_reuse_copies_only_login_and_never_replaces_existing_credentials(self):
        source = workbench.project_for(1)
        target = workbench.assign_account_slots(
            workbench.Project("other", "Second repo", self.root / "other", (source.profile,)), [source])
        profiles = self.profiles / "profiles"
        source_path = profiles / source.profile / workbench.credential_paths[0]
        source_path.parent.mkdir(parents=True)
        source_path.write_text('{"token":{"refresh_token":"fixture-value"}}')
        (source_path.parent / "history.jsonl").write_text("fixture history")
        destination = profiles / target.runtime_profile(source.profile) / workbench.credential_paths[0]
        self.assertTrue(workbench.reuse_account_login(target, source.profile, [source, target], self.profiles))
        self.assertEqual(json.loads(destination.read_text()), json.loads(source_path.read_text()))
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse((destination.parent / "history.jsonl").exists())
        destination.write_text('{"token":{"refresh_token":"own-login"}}')
        self.assertFalse(workbench.reuse_account_login(target, source.profile, [source, target], self.profiles))
        self.assertIn("own-login", destination.read_text())

    def test_list_project_conversations_filters_by_repo_and_extracts_title(self):
        project = workbench.project_for(1)
        project.repo.mkdir(parents=True)
        other_repo = self.root / "other-repo"
        other_repo.mkdir()
        app_dir = self.profiles / "profiles" / project.profile / ".gemini" / "antigravity-cli"
        app_dir.mkdir(parents=True)
        for cid in ("conv-old", "conv-new", "conv-other"):
            transcript = app_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text('{"type":"USER_INPUT","content":"<USER_REQUEST>\\nFix login flow\\n</USER_REQUEST>"}\n')
        (app_dir / "history.jsonl").write_text(
            json.dumps({"conversationId": "conv-old", "workspace": str(project.repo), "display": "Initial chat", "timestamp": 1000}) + "\n"
            + json.dumps({"conversationId": "conv-new", "workspace": str(project.repo), "display": "", "timestamp": 2000}) + "\n"
            + json.dumps({"conversationId": "conv-other", "workspace": str(other_repo), "display": "Other repo", "timestamp": 3000}) + "\n"
        )
        pointer = workbench.session_file(project, project.profile)
        pointer.parent.mkdir(parents=True)
        pointer.write_text("conv-old\n")

        sessions = workbench.list_project_conversations(project, project.profile, self.profiles)
        self.assertEqual([item["id"] for item in sessions], ["conv-new", "conv-old"])
        self.assertEqual(sessions[0]["title"], "Fix login flow")
        self.assertFalse(sessions[0]["current"])
        self.assertEqual(sessions[1]["title"], "Initial chat")
        self.assertTrue(sessions[1]["current"])

    def test_reuse_updates_seeded_slot_after_relogin_while_keeping_own_login(self):
        source = workbench.project_for(1)
        target = workbench.assign_account_slots(
            workbench.Project("other", "Second repo", self.root / "other", (source.profile,)), [source])
        profiles = self.profiles / "profiles"
        source_path = profiles / source.profile / workbench.credential_paths[0]
        source_path.parent.mkdir(parents=True)
        source_path.write_text('{"token":{"refresh_token":"old-token"}}')
        destination = profiles / target.runtime_profile(source.profile) / workbench.credential_paths[0]

        self.assertTrue(workbench.reuse_account_login(target, source.profile, [source, target], self.profiles))
        os.utime(destination, (1000, 1000))
        source_path.write_text('{"token":{"refresh_token":"new-token"}}')
        os.utime(source_path, (2000, 2000))

        self.assertTrue(workbench.reuse_account_login(target, source.profile, [source, target], self.profiles))
        self.assertEqual(json.loads(destination.read_text())["token"]["refresh_token"], "new-token")

        destination.write_text('{"token":{"refresh_token":"own-login"}}')
        source_path.write_text('{"token":{"refresh_token":"third-token"}}')
        os.utime(source_path, (3000, 3000))
        self.assertFalse(workbench.reuse_account_login(target, source.profile, [source, target], self.profiles))
        self.assertIn("own-login", destination.read_text())

    def test_list_project_conversations_filters_by_repo_and_extracts_title(self):
        project = workbench.project_for(1)
        project.repo.mkdir(parents=True)
        other_repo = self.root / "other-repo"
        other_repo.mkdir()
        app_dir = self.profiles / "profiles" / project.profile / ".gemini" / "antigravity-cli"
        app_dir.mkdir(parents=True)
        for cid in ("conv-old", "conv-new", "conv-other"):
            transcript = app_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text('{"type":"USER_INPUT","content":"<USER_REQUEST>\\nFix login flow\\n</USER_REQUEST>"}\n')
        (app_dir / "history.jsonl").write_text(
            json.dumps({"conversationId": "conv-old", "workspace": str(project.repo), "display": "Initial chat", "timestamp": 1000}) + "\n"
            + json.dumps({"conversationId": "conv-new", "workspace": str(project.repo), "display": "", "timestamp": 2000}) + "\n"
            + json.dumps({"conversationId": "conv-other", "workspace": str(other_repo), "display": "Other repo", "timestamp": 3000}) + "\n"
        )
        pointer = workbench.session_file(project, project.profile)
        pointer.parent.mkdir(parents=True)
        pointer.write_text("conv-old\n")

        sessions = workbench.list_project_conversations(project, project.profile, self.profiles)
        self.assertEqual([item["id"] for item in sessions], ["conv-new", "conv-old"])
        self.assertEqual(sessions[0]["title"], "Fix login flow")
        self.assertFalse(sessions[0]["current"])
        self.assertEqual(sessions[1]["title"], "Initial chat")
        self.assertTrue(sessions[1]["current"])


if __name__ == "__main__":
    unittest.main()
