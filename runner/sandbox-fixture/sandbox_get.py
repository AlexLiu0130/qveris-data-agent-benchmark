import json
from pathlib import Path
import sys


request = json.loads(sys.stdin.readline())
if type(request) is not dict or set(request) != {"protocol_version", "request_id", "query"} or request.get("protocol_version") != "sandbox-get-input/v1":
    raise SystemExit(2)

# The fixture has no repository layer or mount.  This check makes an accidental
# Oracle bind mount fail its focused Docker smoke rather than silently passing.
reason = "oracle_visible" if Path("/benchmark-oracle-canary").exists() else "semantic_fixture_offline"
print(json.dumps({"schema_version": "get-response/v1", "status": "error", "data": None, "clarification": None, "terminal_reason": reason}, separators=(",", ":")))
