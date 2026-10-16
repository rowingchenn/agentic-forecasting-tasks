#!/usr/bin/env bash
# Install the run day's frozen structured input X_d, staged into assets/x before the run.
# It stays root-owned and read-only so verification can trust task.json.
set -euo pipefail

test -f assets/x/task.json
mkdir -p /home/user/input
rm -rf /home/user/input/x
cp -r assets/x /home/user/input/x
chown -R root:root /home/user/input/x
chmod -R a+rX,go-w /home/user/input/x
