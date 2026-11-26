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
spec = importlib.util.spec_from_file_location("desktop_workbench", scripts / "agy-herdr-projects.py")
workbench = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = workbench
spec.loader.exec_module(workbench)

from desktop_api import convert_repo, desktop_main, snapshot


class DesktopApiTests(unittest.TestCase):
    def test_counts_a_shared_login_once_and_returns_no_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = workbench.Project("one", "One", root / "one", ("shared",))
            second = workbench.assign_account_slots(
                workbench.Project("two", "Two", root / "two", ("shared", "backup")), [first])
            credential = root / "profiles" / "shared" / workbench.credential_paths[0]
            credential.parent.mkdir(parents=True)
            credential.write_text('{"token":{"refresh_token":"private-test-value"}}')
            with patch.object(workbench, "load_projects", return_value=[first, second]), \
                    patch.object(workbench, "accounts_root", return_value=root), \
                    patch.object(workbench, "server_environment", return_value={}), \
                    patch.object(workbench, "herdr", return_value={"running": False}):
                result = snapshot(workbench)
            self.assertEqual([account["name"] for account in result["accounts"] if account["signed_in"]], ["shared"])
            self.assertEqual(len(result["projects"]), 2)
            self.assertNotIn("private-test-value", json.dumps(result))

    def test_converts_wsl_paths_delivered_by_explorer(self):
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": ""}, clear=False):
            self.assertEqual(convert_repo(r"\\wsl.localhost\Ubuntu\home\someone\my repo"),
                             Path("/home/someone/my repo"))
            self.assertEqual(convert_repo(r"\\wsl.localhost\Ubuntu-24.04\home\someone\my repo"),
                             Path("/home/someone/my repo"))
            with self.assertRaises(ValueError):
                convert_repo(r"\\wsl.localhost\Debian\home\someone\my repo")
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu-24.04"}, clear=False):
            self.assertEqual(convert_repo(r"\\wsl.localhost\Ubuntu-24.04\home\someone\my repo"),
                             Path("/home/someone/my repo"))
            with self.assertRaises(ValueError):
                convert_repo(r"\\wsl.localhost\Ubuntu\home\someone\my repo")

    def test_validate_account_and_assign_new_account(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Tên tài khoản"):
                desktop_main(workbench, ["add", "Bad", folder, "user@gmail.com"])
            with patch("desktop_api.launch") as launch_mock, \
                    patch("desktop_api.snapshot", return_value={"accounts": [], "projects": [], "server_running": False, "client_attached": False}):
                desktop_main(workbench, ["assign", "one", "new-acc"])
                launch_mock.assert_called_once_with(workbench, "--assign", "one", "new-acc")


if __name__ == "__main__":
    unittest.main()
