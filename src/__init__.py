"""
Smart Grocery & Meal Budget Assistant — orchestrator package.

New here? Read the README first, then follow the request through the code
in this order:

    handler.py          Lambda entrypoint — parses the HTTP event
    runner.py           builds the graph and runs one turn
    graph/build.py       the state machine (start here to see the whole flow)
    graph/nodes/         what each step in that state machine actually does
    schemas/contract.py  the request/response shapes everything above produces

`models/` and `retrieval/` are the two swappable boundaries (LLM calls and
price lookups) that the graph nodes depend on without ever importing boto3
directly — see their own docstrings for why that separation matters.
"""
