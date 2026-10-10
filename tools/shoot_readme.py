"""README 截图重拍脚本（零依赖，直连 Edge CDP）。

用途：README 头图是旧版截图（电脑端布局改过、补短板曾推两杯饮料），
需要按当前代码重新拍一组干净、清晰的图。

设计要点：
- 手机端用 2x deviceScaleFactor 拍（430 CSS px -> 860 物理像素），保证 retina 清晰度。
- 电脑端 2x（1120 CSS px -> 2240 物理像素），README 里缩到 640 宽显示 => 约 3.5x 有效采样。
- 拍完自动裁掉纯白边缘，避免大片留白。
- 全程只读页面，不改状态；末尾恢复时区（如果改过）。
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

# 本机 CDP / 预览服务必须绕过系统代理（否则 http_proxy 会把 127.0.0.1 也代理走 -> 502）
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)

PORT = int(os.environ.get("CDP_PORT", "9223"))
BASE = f"http://127.0.0.1:{PORT}"
SITE = os.environ.get("SITE", "http://127.0.0.1:8137/")
OUT = os.environ.get("OUT", "assets")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = os.path.join(os.environ.get("TEMP", "."), "mcd-shot-profile")


# ---------------------------------------------------------------- CDP plumbing
class CDP:
    def __init__(self, ws_url):
        self.ws = _ws_connect(ws_url)
        self._id = 0
        self._events = []

    def send(self, method, params=None, session=None, timeout=30):
        self._id += 1
        mine = self._id
        msg = {"id": mine, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = json.loads(self.ws.recv())
            if data.get("id") == mine:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            if "method" in data:
                self._events.append(data)
        raise TimeoutError(method)

    def wait_event(self, method, session=None, timeout=30):
        """等某个 CDP 事件；期间到达的消息缓存起来不丢。"""
        end = time.time() + timeout
        while time.time() < end:
            for i, d in enumerate(self._events):
                if d.get("method") == method and (session is None or d.get("sessionId") == session):
                    return self._events.pop(i)
            try:
                self.ws.sock.settimeout(max(0.5, end - time.time()))
                data = json.loads(self.ws.recv())
            except (TimeoutError, socket.timeout):
                break
            if data.get("method") == method and (session is None or data.get("sessionId") == session):
                return data
            if "method" in data:
                self._events.append(data)
        raise TimeoutError(method)


def _ws_connect(url):
    """极简 WebSocket 客户端（RFC6455 帧，文本/二进制），够 CDP 用。"""
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    sock = socket.create_connection((u.hostname, u.port or 80), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    path = u.path + (("?" + u.query) if u.query else "")
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    sock.settimeout(None)
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b"101" not in resp.split(b"\r\n")[0]:
        raise RuntimeError("WS handshake failed: " + resp[:200].decode("latin1"))
    return _WS(sock)


class _WS:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("ws closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        while True:
            b1, b2 = self._read(2)
            opcode = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            data = self._read(n)
            if opcode == 0x1:
                return data.decode()
            if opcode == 0x8:
                raise ConnectionError("ws close frame")
            # 其余（ping/pong/continuation）忽略


def launch_edge():
    """自己拉起 Edge 并返回 Popen（脚本结束时主动关掉），保证生命周期完整。"""
    if not os.path.exists(EDGE):
        sys.exit("找不到 Edge: " + EDGE)
    args = [
        EDGE, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--hide-scrollbars", "--force-device-scale-factor=1",
        "--no-proxy-server", "--window-size=1200,900",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{BASE}/json/version", timeout=2).read()
            return proc
        except Exception:
            if proc.poll() is not None:
                sys.exit(f"Edge 立刻退出了，returncode={proc.returncode}")
            time.sleep(0.5)
    try:
        proc.kill()
    except Exception:
        pass
    sys.exit("Edge 调试端口没起来")


def new_target(url):
    req = urllib.request.Request(f"{BASE}/json/new?{url}", method="PUT")
    return json.load(urllib.request.urlopen(req, timeout=10))


def wait_ready(cdp, session, timeout=30):
    """轮询 readyState，比等 loadEventFired 稳（attach 可能错过事件）。"""
    cdp.send("Page.enable", session=session)
    end = time.time() + timeout
    while time.time() < end:
        try:
            if evaljs(cdp, session, "document.readyState", timeout=5) == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.4)
    raise TimeoutError("readyState 一直没到 complete")


def evaljs(cdp, session, expr, timeout=25):
    r = cdp.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                      "returnByValue": True}, session=session, timeout=timeout)
    if r.get("exceptionDetails"):
        raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
    return r.get("result", {}).get("value")


def shot(cdp, session, path, full=False):
    if full:
        m = cdp.send("Page.getLayoutMetrics", session=session)
        cs = m.get("cssContentSize") or m.get("contentSize")
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": int(cs["width"]), "height": int(cs["height"]),
            "deviceScaleFactor": SCALE, "mobile": MOBILE}, session=session)
        time.sleep(0.6)
    r = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": full,
                                            "fromSurface": True}, session=session, timeout=60)
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    return path


# --------------------------------------------------------------- image helper
def trim_white(path, tol=6, pad=0):
    """裁掉四周纯白边（可选，需 Pillow；没有就原样返回）。"""
    try:
        from PIL import Image, ImageChops
    except Exception:
        return path
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda p: 255 if p > tol else 0)
    box = diff.getbbox()
    if not box:
        return path
    l, t, r, b = box
    l, t = max(0, l - pad), max(0, t - pad)
    r, b = min(im.width, r + pad), min(im.height, b + pad)
    if (l, t, r, b) != (0, 0, im.width, im.height):
        im.crop((l, t, r, b)).save(path)
    return path


# ------------------------------------------------------------------- scenarios
DESKTOP_W, DESKTOP_H = 1040, 1100
PHONE_W, PHONE_H = 430, 932
SCALE = 2
MOBILE = False

# 截图用的固定餐盘（与 README 文案一致：巨无霸 + 薯条 + 可乐）
DEMO_TRAY = [{"name": "巨无霸", "qty": 1},
             {"name": "薯条", "qty": 1},
             {"name": "可乐", "qty": 1}]

RESET_TRAY = """
(async () => {
  const names = %s;
  const order = %s.map(n => ({name:n, qty:1})).filter(i => state.catalog[i.name]);
  state.items = order.length ? order : state.items;
  state.mode = 'weichi';
  state.slot = '正餐';
  revertAdopted();
  await refresh();
  return state.items.map(i => i.name).join(',');
})()
""" % (json.dumps([n["name"] for n in DEMO_TRAY], ensure_ascii=False), json.dumps(DEMO_TRAY, ensure_ascii=False))


def set_viewport(cdp, session, w, h, mobile, scale):
    global SCALE, MOBILE
    SCALE, MOBILE = scale, mobile
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": scale, "mobile": mobile},
             session=session)
    time.sleep(0.8)


APPLY = """
(async () => {
  // 等应用就绪
  for (let i=0;i<80;i++){ if (window.state && document.querySelector('.mcard')) break; await new Promise(r=>setTimeout(r,150)); }
  return true;
})()
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    proc = launch_edge()
    results = []
    try:
        tgt = new_target("about:blank")
        cdp = CDP(tgt["webSocketDebuggerUrl"])
        session = cdp.send("Target.attachToTarget",
                           {"targetId": tgt["id"], "flatten": True})["sessionId"]

        cdp.send("Page.navigate", {"url": SITE}, session=session)
        wait_ready(cdp, session)
        evaljs(cdp, session, APPLY)
        print("页面已就绪:", evaljs(cdp, session, "document.title"))
        print("固定餐盘:", evaljs(cdp, session, RESET_TRAY))

        # ---------- 1) 电脑端整页 ----------
        set_viewport(cdp, session, DESKTOP_W, DESKTOP_H, False, 2)
        time.sleep(2.0)
        p = os.path.join(OUT, "00-电脑端-展示台布局.png")
        shot(cdp, session, p, full=True)
        results.append(p)
        print("已拍", p)

        # ---------- 2) 手机端整页 ----------
        set_viewport(cdp, session, PHONE_W, PHONE_H, True, 2)
        evaljs(cdp, session, "window.scrollTo(0,0); document.querySelector('.scr').scrollTop=0; true")
        time.sleep(1.5)
        p = os.path.join(OUT, "01-手机端-首屏.png")
        shot(cdp, session, p, full=True)
        results.append(p)
        print("已拍", p)

        # ---------- 3) 手机端：滚到「智能替换 + 补短板」区域 ----------
        evaljs(cdp, session, """
        (() => {
          const host = document.querySelector('.scr');
          const el = document.querySelector('#gapbox') || document.querySelector('.gapwrap');
          if (host && el) { host.scrollTop = el.offsetTop - 150; }
          else if (el) { el.scrollIntoView({block:'start'}); }
          return true;
        })()
        """)
        time.sleep(1.2)
        p = os.path.join(OUT, "02-手机端-补短板与替换.png")
        shot(cdp, session, p, full=False)
        results.append(p)
        print("已拍", p)

        # 复核：补短板 role 分布（确认没有出现两杯饮料）
        roles = evaljs(cdp, session, """
        (() => {
          const cat = state.catalog || {};
          const rows = [...document.querySelectorAll('#gapbox .srow, #gapbox .gaprow')];
          const names = rows.map(r => (r.querySelector('.gname,.gtxt,.from')||{}).textContent || '');
          const picked = [...document.querySelectorAll('#gapbox [data-gapadd]')].map(b => b.dataset.gapadd);
          return JSON.stringify({buttons: picked, roles: picked.map(n => (cat[n]&&cat[n].role)||'?')});
        })()
        """)
        print("补短板:", roles)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("Edge 已关闭")

    for p in results:
        trim_white(p, pad=6)
    print("完成:", json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
