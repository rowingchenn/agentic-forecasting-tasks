#!/usr/bin/env bash
# Install the replayed run day: its task.json and the materials the live agents saved.
# The agent gets read-only copies; this stage directory ends up root-only.
set -euo pipefail

mkdir -p /home/user/input
rm -rf /home/user/input/task.json /home/user/input/archive
cp assets/task.json /home/user/input/task.json
mkdir -p assets/archive  # empty when no live agent saved anything; Git keeps no empty folders
cp -r assets/archive /home/user/input/archive
chown -R root:root /home/user/input
chmod -R a+rX,go-w /home/user/input
chmod -R go-rwx "$ALE_STAGE_DIR"
