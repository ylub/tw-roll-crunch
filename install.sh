#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
command -v task >/dev/null 2>&1 || { echo "Taskwarrior not found." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Python 3 not found." >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
    echo "Python 3.10 or newer is required." >&2
    exit 1
}
data=${TASKDATA:-$HOME/.task}
hooks=$data/hooks
bin=${TASKWARRIOR_ROLL_BIN:-$HOME/.local/bin}
mkdir -p "$hooks"
mkdir -p "$bin"

for target in on-exit-roll.py on-modify.crunch.py on-add.crunch.py; do
    [ ! -e "$hooks/$target" ] && [ ! -L "$hooks/$target" ] || {
        echo "Refusing to replace existing hook: $hooks/$target" >&2
        exit 1
    }
done

[ ! -e "$bin/task_roll_help" ] && [ ! -L "$bin/task_roll_help" ] || {
    echo "Refusing to replace existing command: $bin/task_roll_help" >&2
    exit 1
}

install -m 755 "$root/hooks/roll.py" "$hooks/on-exit-roll.py"
install -m 755 "$root/hooks/crunch.py" "$hooks/on-modify.crunch.py"
ln -s on-modify.crunch.py "$hooks/on-add.crunch.py"
install -m 755 "$root/task_roll_help" "$bin/task_roll_help"

echo "Hooks installed in $hooks"
echo "Roll help installed at $bin/task_roll_help"
echo "Next, add this line to your Taskwarrior config (usually ~/.taskrc):"
echo "include $root/config/taskrc.example"
echo "Then verify the hooks with: task diagnostics"
