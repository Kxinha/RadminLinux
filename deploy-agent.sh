#!/usr/bin/env bash
# Installs (or reinstalls) the agent in the running VM: copies agent/* and the
# shim over SMB, registers the scheduled tasks and starts the API.
#   ./deploy-agent.sh          only when the VM runs an older agent
#   ./deploy-agent.sh --force  always
SELF="$(cd "$(dirname "$0")" && pwd)"
source "$SELF/env.sh"
exec "$RADMIN_PY" "$RADMIN_APP/agent.py" ensure "$@"
