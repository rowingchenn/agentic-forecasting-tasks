#!/usr/bin/env bash
# Install the replayed run day: X_d and the live agents' materials, staged into assets/.
# Both stay root-owned and read-only so verification can trust task.json.
set -euo pipefail

test -f assets/x/task.json
test -d assets/archive
mkdir -p /home/user/input
rm -rf /home/user/input/x /home/user/input/archive
cp -r assets/x /home/user/input/x
cp -r assets/archive /home/user/input/archive
chown -R root:root /home/user/input/x /home/user/input/archive
chmod -R a+rX,go-w /home/user/input/x /home/user/input/archive
