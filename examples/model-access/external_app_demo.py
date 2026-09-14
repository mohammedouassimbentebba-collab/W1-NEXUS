"""Minimal external application model bridge over stdio."""

import json
import sys

message = json.loads(sys.stdin.readline())
request = message["request"]
print(json.dumps({
    "output_type": request["task"]["expected_output_type"],
    "payload": {
        "source": "external-application",
        "received_task": request["task"]["task_id"]
    },
    "context_fields_used": list(request.get("context", {}))
}))
