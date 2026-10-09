"""麦当劳 MCP 探针：查看工具入参 schema + 试调取数。

用法：
    python tools/mcp-probe.py schema <tool1> [tool2 ...]   # 打印工具的输入参数结构
    python tools/mcp-probe.py call <tool> '<json args>'    # 实际调用并打印返回

Token 从环境变量 MCD_MCP_TOKEN 读取（也兼容 ~/.workbuddy/mcp.json），不打印、不落盘。
"""
import json
import os
import re
import sys
import urllib.request

MCP_URL = "https://mcp.mcd.cn"
CFG = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def _token():
    env = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if env:
        return env if env.lower().startswith("bearer") else "Bearer " + env
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["mcpServers"]["mcd-mcp"]["headers"]["Authorization"]


def rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": _token(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    # SSE 响应：逐行剥掉 "data: " 前缀，取最后一条能解析成 JSON 的
    payloads = [ln[6:] if ln.startswith("data: ") else ln
                for ln in raw.strip().splitlines() if ln.strip()]
    for chunk in reversed(payloads):
        try:
            return json.loads(chunk)
        except Exception:
            continue
    raise RuntimeError("无法解析响应: " + raw[:400])


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd = sys.argv[1]

    if cmd == "schema":
        tools = rpc("tools/list", {}).get("result", {}).get("tools", [])
        want = set(sys.argv[2:])
        print("工具总数:", len(tools))
        for t in tools:
            if t["name"] not in want:
                continue
            print("\n===", t["name"], "===")
            print("desc:", (t.get("description") or t.get("title") or "")[:160])
            schema = t.get("inputSchema", {})
            props = schema.get("properties", {})
            req = set(schema.get("required", []))
            if not props:
                print("参数: (无)")
            for k, v in props.items():
                mark = "*" if k in req else " "
                desc = (v.get("description") or "")[:80]
                print(f"  {mark} {k} <{v.get('type')}> {desc}")

    elif cmd == "call":
        args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        res = rpc("tools/call", {"name": sys.argv[2], "arguments": args})
        print(json.dumps(res, ensure_ascii=False, indent=2)[:4000])

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
