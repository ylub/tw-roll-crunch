"""Exercise installed copies against temporary Taskwarrior databases."""

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
TASK = next((path for path in ("/opt/homebrew/bin/task", "/usr/local/bin/task")
             if Path(path).is_file()), shutil.which("task"))


@unittest.skipUnless(TASK, "Taskwarrior is not installed")
class InstalledTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="roll-crunch-tests-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.data = self.base / "data"
        self.bin = self.base / "bin"
        self.env = os.environ.copy()
        self.env.pop("PYTHONPATH", None)
        self.env.update(TASKDATA=str(self.data), TASKRC=str(self.base / "taskrc"),
                        TASKWARRIOR_ROLL_BIN=str(self.bin), TASKWARRIOR_ROLL_TASK=TASK,
                        PATH=str(self.bin) + os.pathsep + self.env["PATH"], NO_COLOR="1")
        self.config = (ROOT / "config/taskrc.example").read_text()
        self.write_config()

    def write_config(self):
        (self.base / "taskrc").write_text(self.config + "\nconfirmation=off\nverbose=nothing\n")

    def run_command(self, *arguments, check=True):
        return subprocess.run(arguments, env=self.env, cwd=self.base, check=check,
                              capture_output=True, text=True)

    def install(self):
        self.run_command("sh", str(ROOT / "install.sh"))

    def tasks(self):
        return json.loads(self.run_command(TASK, "rc.hooks=0", "rc.json.array=on", "export").stdout)

    def add(self, description, *fields):
        self.run_command(TASK, "add", description, *fields)
        return next(task for task in self.tasks() if task["description"] == description)

    def test_installed_roll_refreshes_scores_and_aliases(self):
        self.install()
        parent = self.add("Parent", "due:2099-01-01")
        child = self.add("Child", "due:2099-01-02", "remaining:1h", "progress:0",
                         f"roll:{parent['uuid']}", "roll_offset:1d")
        self.assertEqual(child["crunch"], "MED")
        self.run_command(TASK, parent["uuid"], "modify", "due:2020-01-01")
        child = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
        self.assertTrue(child["due"].startswith("20200102"))
        self.assertEqual(child["crunch"], "CRITICAL")
        self.assertEqual(child["crunch_display"], "CRITICAL")
        self.run_command(TASK, parent["uuid"], "modify", "due:2099-01-01")
        child = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
        self.assertEqual(child["crunch"], "MED")
        for arguments, expected in ((("roll", "help"), "Taskwarrior Roll"),
                                    (("chain",), "CHAIN —"),
                                    (("rock", "help"), "Taskwarrior Rock")):
            result = self.run_command(TASK, *arguments)
            self.assertIn(expected, result.stdout, result.stderr)
        result = self.run_command("sh", str(ROOT / "install.sh"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to replace existing hook", result.stderr)

    def test_installed_chain_clears_old_scores(self):
        self.install()
        selected = [self.add(name, "due:2020-01-01", "remaining:1h", "progress:25")
                    for name in ("First", "Second")]
        self.assertTrue(all(task["crunch"] == "HIGH" for task in selected))
        source = self.base / "chain.txt"
        source.write_text("\n".join(f"{task['uuid']}:{task['description']}" for task in selected))
        start = dt.date(2099, 1, 1)
        while start.weekday() > 4:
            start += dt.timedelta(days=1)
        for command in ("roll", "rock"):
            result = self.run_command(TASK, command, "chain", str(source), "--start", str(start),
                                      "--finish", str(start + dt.timedelta(days=7)),
                                      "--remaining", "4h", "--apply")
            self.assertIn("APPLIED: 2 tasks", result.stdout, result.stderr)
        self.assertEqual(next(task for task in self.tasks() if task["uuid"] == selected[-1]["uuid"])["roll_fixed"],
                         "finish-line")
        for task in self.tasks():
            self.assertNotIn("crunch", task)
            self.assertNotIn("crunch_display", task)
            self.assertIn(task["description"], ("First", "Second"))

    def test_installed_roll_without_crunch_udas(self):
        self.config = "\n".join(line for line in self.config.splitlines()
                                if not line.startswith(("uda.crunch.", "uda.crunch_display.")))
        self.write_config()
        self.install()
        parent = self.add("Parent", "due:2099-01-01")
        child = self.add("Child", "due:2099-01-02", "remaining:1h",
                         f"roll:{parent['uuid']}", "roll_offset:1d")
        self.run_command(TASK, parent["uuid"], "modify", "due:2020-01-01")
        child = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
        self.assertEqual(child["description"], "Child")
        self.assertTrue(child["due"].startswith("20200102"))

    def test_standalone_installs(self):
        hooks = self.data / "hooks"
        hooks.mkdir(parents=True)
        self.bin.mkdir()
        shutil.copy2(ROOT / "roll_crunch.py", self.data / "roll_crunch.py")
        # Crunch only: no Roll hook or CLI imports available.
        shutil.copy2(ROOT / "hooks/crunch.py", hooks / "on-modify.crunch.py")
        (hooks / "on-add.crunch.py").symlink_to("on-modify.crunch.py")
        task = self.add("Crunch only", "due:2020-01-01", "remaining:1h")
        self.assertEqual(task["crunch"], "CRITICAL")
        # Switch this disposable installation to Roll only.
        (hooks / "on-modify.crunch.py").unlink()
        (hooks / "on-add.crunch.py").unlink()
        shutil.copy2(ROOT / "hooks/roll.py", hooks / "on-exit-roll.py")
        shutil.copy2(ROOT / "roll_calendar.py", self.data / "roll_calendar.py")
        for name in ("roll_calendar.py", "roll_crunch.py", "task_roll_help", "task_chain", "task_chains", "task_rock"):
            shutil.copy2(ROOT / name, self.bin / name)
        parent = self.add("Parent", "due:2099-01-01")
        child = self.add("Roll only", "due:2099-01-02", "remaining:1h",
                         f"roll:{parent['uuid']}", "roll_offset:1d")
        self.run_command(TASK, parent["uuid"], "modify", "due:2020-01-01")
        child = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
        self.assertEqual(child["crunch"], "CRITICAL")
        self.run_command(TASK, "chain")

    def test_helper_collision_refuses_before_installing_hooks(self):
        for location in (self.data, self.bin):
            with self.subTest(location=location):
                location.mkdir(parents=True, exist_ok=True)
                helper = location / "roll_crunch.py"
                helper.write_text("preserve me")
                result = self.run_command("sh", str(ROOT / "install.sh"), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(helper.read_text(), "preserve me")
                self.assertFalse((self.data / "hooks/on-exit-roll.py").exists())
                helper.unlink()


if __name__ == "__main__":
    unittest.main()
