#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v task >/dev/null 2>&1 || { echo "Taskwarrior not found." >&2; exit 1; }
data=${TASKDATA:-$HOME/.task}
hooks=$data/hooks
mkdir -p "$hooks"

for target in on-exit-roll.py on-modify.crunch.py on-add.crunch.py; do
    [ ! -e "$hooks/$target" ] && [ ! -L "$hooks/$target" ] || {
        echo "Refusing to replace existing hook: $hooks/$target" >&2
        exit 1
    }
done

install -m 755 "$root/hooks/roll.py" "$hooks/on-exit-roll.py"
install -m 755 "$root/hooks/crunch.py" "$hooks/on-modify.crunch.py"
ln -s on-modify.crunch.py "$hooks/on-add.crunch.py"

echo "Hooks installed in $hooks"
echo "Add needed settings from $root/config/taskrc.example to ~/.taskrc"
