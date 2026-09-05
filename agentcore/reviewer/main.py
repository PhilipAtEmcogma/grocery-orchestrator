"""
Zip-root entrypoint for AgentCore Runtime direct code deployment.

AgentCore's `entryPoint` names a .py file at the ROOT of the deployment zip
(the runtime mounts the zip at /var/task and puts it first on sys.path). Our
real handler lives at `agentcore/reviewer/app.py` and imports `src...`, so the
deployable zip is built with this file, `agentcore/`, and `src/` all at the
root -- and this shim just hands off to the real server. Keeping the handler in
`agentcore/reviewer/app.py` (not here) means the offline tests, the simulation,
and the deployed Runtime all run the SAME code; this file only exists because
the platform wants a root-level filename.
"""

from agentcore.reviewer.app import main

if __name__ == "__main__":
    main()
