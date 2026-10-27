"""Exercise installed copies against temporary Taskwarrior databases."""

import datetime as dt
import json
import os
import pty
import select
import time
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zoneinfo import ZoneInfo

from roll_calendar import local_zone


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

    def confirmed_command(self, *arguments, shell_line=None):
        master, slave = pty.openpty()
        process = subprocess.Popen(arguments, env=self.env, cwd=self.base,
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b""
        deadline = time.monotonic() + 15
        answered = False
        exited = False
        if shell_line:
            os.write(master, (shell_line + "\n").encode())
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    if not answered and b"Apply these changes?" in output:
                        os.write(master, b"y\n")
                        answered = True
                    if shell_line and not exited and b"APPLIED:" in output:
                        os.write(master, b"exit\n")
                        exited = True
                elif process.poll() is not None:
                    break
            self.assertTrue(answered, output.decode())
            self.assertEqual(process.wait(timeout=2), 0, output.decode())
            return output.decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


    def test_installed_chain_edit_preview_and_confirm(self):
        self.install()
        root = self.add("Root", "due:2099-01-01")
        child = self.add("Child", "due:2099-01-03", "remaining:1h",
                         f"roll:{root['uuid']}", "roll_offset:P2D")
        finish = self.add("Finish", "due:2099-01-10", f"roll:{child['uuid']}",
                          "roll_offset:P2D", "roll_fixed:finish-line")
        before = self.tasks()
        preview = self.run_command(TASK, "chain", "1", "modify", "roll_offset:P1D")
        self.assertIn("Preview only", preview.stdout)
        self.assertEqual(self.tasks(), before)
        output = self.confirmed_command(TASK, "chain", "1", "modify", "roll_offset:P1D")
        self.assertIn("APPLIED: 2 tasks", output)
        tasks = {task["uuid"]: task for task in self.tasks()}
        self.assertNotIn("roll_manual", tasks[root["uuid"]])
        self.assertTrue(tasks[child["uuid"]]["due"].startswith("20990102"))
        for uuid in (child["uuid"], finish["uuid"]):
            self.assertEqual(tasks[uuid]["roll_manual"], "yes")
        self.assertEqual(tasks[finish["uuid"]]["due"], finish["due"])
        output = self.confirmed_command(TASK, "chain", "1", "modify", "priority:H", "+bulk")
        self.assertIn("APPLIED: 3 tasks", output)
        self.assertTrue(all(task.get("priority") == "H" and "bulk" in task.get("tags", [])
                            for task in self.tasks()))
        output = self.confirmed_command(
            TASK, "chain", "1", "modify", "progress:25.5", "project:bulk.demo",
            "remaining:2h", "phoenix:90m", "phoenix_wait:yes", "phoenix_count:2",
        )
        self.assertIn("APPLIED: 3 tasks", output)
        for task in self.tasks():
            self.assertEqual(task["progress"], 25.5)
            self.assertEqual(task["project"], "bulk.demo")
            self.assertEqual(task["remaining"], "PT2H")
            self.assertEqual(task["phoenix"], "PT1H30M")
            self.assertEqual(task["phoenix_wait"], "yes")
            self.assertEqual(task["phoenix_count"], 2)
        rejected = self.run_command(TASK, "chain", "1", "modify", "--apply", check=False)
        self.assertIn("Unsupported or duplicate modification: --apply", rejected.stderr)


    def test_installed_roll_at_bulk_preview_completion_and_clear(self):
        self.env["TZ"] = "America/Chicago"
        self.config += "\nroll.calendar.2026-10-27=1\n"
        self.write_config()
        self.install()
        local = local_zone()
        utc = ZoneInfo("UTC")
        start = dt.datetime(2026, 10, 27, 10, tzinfo=local).astimezone(utc)
        root = self.add("Root", "due:" + start.strftime("%Y%m%dT%H%M%SZ"))
        child = self.add("Child", f"roll:{root['uuid']}", "roll_offset:P1D")
        arguments = (TASK, "chain", "1", "modify", "roll_offset:P1D", "roll_at:10:00")
        before = self.tasks()
        preview = self.run_command(*arguments)
        self.assertIn("Preview only", preview.stdout)
        after = self.tasks()
        for task in before + after:
            task.pop("urgency", None)  # Computed urgency changes as time passes.
        self.assertEqual(before, after)
        output = self.confirmed_command(*arguments)
        self.assertIn("APPLIED: 1 tasks", output)
        edited = next(t for t in self.tasks() if t["uuid"] == child["uuid"])
        self.assertEqual(edited["roll_offset"], "P1D")
        self.assertEqual(edited["roll_at"], "10:00")
        expected = dt.datetime(2026, 10, 28, 10, tzinfo=local).astimezone(utc)
        self.assertEqual(edited["due"], expected.strftime("%Y%m%dT%H%M%SZ"))
        detail = self.run_command(TASK, "chain", "1").stdout
        self.assertIn("2026-10-28 10:00", detail)
        self.assertIn("@10:00", detail)
        self.run_command(TASK, root["uuid"], "done")
        completed = next(t for t in self.tasks() if t["uuid"] == root["uuid"])
        edited = next(t for t in self.tasks() if t["uuid"] == child["uuid"])
        end = dt.datetime.strptime(completed["end"], "%Y%m%dT%H%M%SZ").replace(tzinfo=utc)
        day = end.astimezone(local).date() + dt.timedelta(days=1)
        if day.weekday() == 5:
            day += dt.timedelta(days=1)
        expected = dt.datetime.combine(day, dt.time(10), tzinfo=local).astimezone(utc)
        self.assertEqual(edited["due"], expected.strftime("%Y%m%dT%H%M%SZ"))
        for field in ("roll", "roll_offset", "roll_at", "roll_manual"):
            self.assertNotIn(field, edited)


    def test_installed_roll_at_help_aliases(self):
        self.install()
        for arguments in (("roll", "help"), ("rock", "help"), ("chain", "--help"),
                          ("roll", "chain", "--help"), ("rock", "chain", "--help")):
            with self.subTest(arguments=arguments):
                output = self.run_command(TASK, *arguments).stdout
                for text in ("roll_at:10:00", "roll_offset:P1D", "roll_at:"):
                    self.assertIn(text, output)


    def test_track_shell_confirms_installed_chain_edit(self):
        self.install()
        root = self.add("Root", "due:2099-01-01")
        child = self.add("Child", "due:2099-01-03", f"roll:{root['uuid']}", "roll_offset:P2D")
        shell_config = self.base / "shell.toml"
        shell_config.write_text('panels = []\n[theme]\ncolor = "never"\n')
        self.env["TASK_WRAPPER_REAL_TASK"] = TASK
        for project, prefix in (("time", ""), ("time", "task "), ("ttt", ""), ("ttt", "task ")):
            project_path = ROOT.parent / project
            if not (project_path / "track_shell.py").is_file():
                continue
            with self.subTest(project=project):
                script = (f"import sys; from pathlib import Path; sys.path.insert(0, {str(project_path)!r}); "
                          f"import track_shell; sys.exit(track_shell.main(Path({str(shell_config)!r})))")
                output = self.confirmed_command(
                    os.sys.executable, "-B", "-c", script,
                    shell_line=prefix + "chain 1 modify roll_offset:P1D remaining:2h progress:50 project:bulk.demo priority:H +bulk phoenix:90m phoenix_wait:yes phoenix_count:2",
                )
                self.assertIn("APPLIED: 1 tasks", output)
                edited = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
                self.assertTrue(edited["due"].startswith("20990102"))
                self.assertEqual(edited["roll_manual"], "yes")
                self.assertEqual(edited["remaining"], "PT2H")
                self.assertEqual(edited["progress"], 50)
                self.assertEqual(edited["project"], "bulk.demo")
                self.assertEqual(edited["priority"], "H")
                self.assertIn("bulk", edited["tags"])
                self.assertEqual(edited["phoenix"], "PT1H30M")
                self.assertEqual(edited["phoenix_wait"], "yes")
                self.assertEqual(edited["phoenix_count"], 2)
                output = self.confirmed_command(
                    os.sys.executable, "-B", "-c", script,
                    shell_line=prefix + "chain 1 modify roll_manual: remaining: progress: project: priority: -bulk phoenix: phoenix_wait: phoenix_count:",
                )
                self.assertIn("APPLIED: 1 tasks", output)
                edited = next(task for task in self.tasks() if task["uuid"] == child["uuid"])
                for field in ("roll_manual", "remaining", "progress", "project", "priority", "phoenix", "phoenix_wait", "phoenix_count"):
                    self.assertNotIn(field, edited)
                self.assertNotIn("bulk", edited.get("tags", []))


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
