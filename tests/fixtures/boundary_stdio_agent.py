"""Offline ACP peer for real pipe, file-service and permission roundtrips."""

import json
import sys


def send(message):
    print(json.dumps(message), flush=True)


def call(mid, method, params):
    send({"jsonrpc": "2.0", "id": mid, "method": method, "params": {"sessionId": "synthetic-session", **params}})
    while line := sys.stdin.readline():
        message = json.loads(line)
        if message.get("id") == mid and "method" not in message:
            return message
    raise SystemExit(0)


def main():
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if "id" not in message:
            continue
        result = {}
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}
        elif method == "session/new":
            result = {"sessionId": "synthetic-session"}
        elif method == "session/prompt":
            path = json.loads(message["params"]["prompt"][0]["text"])["path"]
            read = call("read", "fs/read_text_file", {"path": path})
            missing = call("missing", "fs/read_text_file", {"path": path + ".missing"})
            params = {
                "toolCall": {"toolCallId": "tool"},
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                ],
            }
            permission = call("permission", "session/request_permission", params)
            text = json.dumps({"read": read["result"], "missing": missing["error"], "permission": permission["result"]})
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "synthetic-session",
                        "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
                    },
                }
            )
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            call("late", "session/request_permission", params)
            continue
        send({"jsonrpc": "2.0", "id": message["id"], "result": result})


if __name__ == "__main__":
    main()
