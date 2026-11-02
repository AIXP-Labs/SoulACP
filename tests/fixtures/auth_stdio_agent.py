"""Synthetic authentication peer; never loads a real provider or credential."""

import json
import sys
import time
from pathlib import Path


def main():
    journal = Path(sys.argv[1])
    scenario = sys.argv[2]
    methods = [{"id": "first", "name": "First"}, {"id": "selected", "name": "Selected"}]
    if scenario == "reverse":
        methods.reverse()
    elif scenario == "duplicate":
        methods.append({"id": "selected", "name": "Duplicate"})
    elif scenario == "none":
        methods = []
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message["method"]
        with journal.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"method": method, "params": message["params"]}) + "\n")
        result = {}
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": methods}
        elif method == "authenticate":
            if scenario == "timeout":
                time.sleep(30)
            if scenario == "disconnect":
                return
            if scenario == "reject":
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32000,
                                "message": "synthetic-private-auth-message",
                            },
                        }
                    ),
                    flush=True,
                )
                continue
        elif method == "session/new":
            result = {"sessionId": "synthetic-auth-session"}
        elif method == "session/prompt":
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "synthetic-auth-session",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "3"},
                            },
                        },
                    }
                ),
                flush=True,
            )
            result = {"stopReason": "end_turn"}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
