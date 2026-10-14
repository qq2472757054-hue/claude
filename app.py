#!/usr/bin/env python3
"""准绳 · 利润核算 —— 本地服务（仅标准库）。

提供 index.html 页面和几个本地接口：
  GET    /api/settings        读取设置（不返回 Key 内容）
  POST   /api/settings        保存设置
  GET    /api/history         历史记录列表
  POST   /api/history         保存一条记录
  DELETE /api/history/<id>    删除一条记录
  POST   /api/ai              AI 解读（由后端调用 ToAPIs）
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PREFERRED_PORT = 8790
APP_DIR = Path(__file__).resolve().parent
INDEX_FILE = APP_DIR / "index.html"
DATA_DIR = Path.home() / ".zhunsheng_profit"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.json"

ENDPOINTS = {
    "toapis.cn": "https://toapis.cn/v1/chat/completions",
    "toapis.com": "https://toapis.com/v1/chat/completions",
}
DEFAULT_SETTINGS = {"api_key": "", "endpoint": "toapis.cn", "model": "gemini-3.7-flash-official"}
SYSTEM_PROMPT = (
    "你是跨境电商运营助手。根据给出的利润核算数据，用通俗中文写不超过150字的解读："
    "利润主要被哪几项成本吃掉，优先可以压缩哪一项，或者售价怎么调整。只引用给出的数字，不编造新数字。"
)
MAX_BODY = 2 * 1024 * 1024
MAX_HISTORY = 500

_lock = threading.Lock()


# ---------- 数据文件 ----------

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def _write_json(path, data, private=False):
    """先写临时文件再替换，避免写到一半损坏。调用方需持有 _lock。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    data = _read_json(SETTINGS_FILE, {})
    if isinstance(data, dict):
        s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS and isinstance(v, str)})
    if s["endpoint"] not in ENDPOINTS:
        s["endpoint"] = DEFAULT_SETTINGS["endpoint"]
    return s


def public_settings(s):
    return {"has_key": bool(s["api_key"]), "endpoint": s["endpoint"], "model": s["model"]}


def load_history():
    data = _read_json(HISTORY_FILE, [])
    return data if isinstance(data, list) else []


# ---------- AI 调用 ----------

class AIError(Exception):
    pass


def _quota_like(text):
    t = text.lower()
    return any(w in t for w in ("quota", "insufficient", "balance", "credit", "余额", "额度"))


def call_ai(payload):
    s = load_settings()
    if not s["api_key"]:
        raise AIError("还没有设置 ToAPIs Key，请先在“设置”里粘贴保存。")
    model = s["model"].strip() or DEFAULT_SETTINGS["model"]
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "stream": False,
        "max_tokens": 600,
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINTS[s["endpoint"]],
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + s["api_key"],
            "Content-Type": "application/json",
            "User-Agent": "zhunsheng-profit/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        code = e.code
        if code == 401:
            raise AIError("Key 无效（401），请在“设置”里检查 ToAPIs Key。")
        if code == 402 or (code in (403, 429) and _quota_like(detail)):
            raise AIError("账户额度不足（%d），请到 ToAPIs 充值后再试。" % code)
        if code == 400:
            raise AIError("请求错误（400），请检查模型名是否正确。" + _short(detail))
        if code == 403:
            raise AIError("没有权限（403），请检查 Key 或模型权限。" + _short(detail))
        if code == 429:
            raise AIError("请求太频繁（429），请稍后再试。")
        raise AIError("AI 服务出错（%d），请稍后再试。" % code + _short(detail))
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise AIError("网络失败，连不上 AI 接口（%s）。可在“设置”里换一个接口地址。" % reason)

    try:
        data = json.loads(raw)
    except ValueError:
        raise AIError("AI 返回的内容无法解析。")
    if isinstance(data, dict) and data.get("error") and not data.get("choices"):
        msg = json.dumps(data["error"], ensure_ascii=False)
        if _quota_like(msg):
            raise AIError("账户额度不足，请到 ToAPIs 充值后再试。")
        raise AIError("AI 返回错误：" + msg[:200])
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("AI 返回格式不对，没有找到解读内容。")
    if not isinstance(content, str) or not content.strip():
        raise AIError("AI 返回内容为空，请重试。")
    return content.strip()


def _short(detail):
    detail = (detail or "").strip()
    if not detail:
        return ""
    try:
        err = json.loads(detail).get("error")
        if isinstance(err, dict):
            detail = err.get("message") or detail
        elif isinstance(err, str):
            detail = err
    except (ValueError, AttributeError):
        pass
    if not isinstance(detail, str) or detail.strip() in ("", "{}"):
        return ""
    return "（" + detail[:150] + "）"


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "ZhunSheng/1.0"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    def _host_ok(self):
        # 防 DNS 重绑定：只接受本机地址访问
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"ok": False, "error": msg})

    def _json_body(self):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("需要 JSON")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ValueError("请求体大小不对")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        if not self._host_ok():
            return self._err(403, "forbidden")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                body = INDEX_FILE.read_bytes()
            except OSError:
                return self._err(500, "找不到 index.html")
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/api/settings":
            return self._send(200, {"ok": True, **public_settings(load_settings())})
        if path == "/api/history":
            with _lock:
                items = load_history()
            return self._send(200, {"ok": True, "items": items})
        self._err(404, "not found")

    def do_POST(self):
        if not self._host_ok():
            return self._err(403, "forbidden")
        path = self.path.split("?", 1)[0]
        try:
            data = self._json_body()
        except (ValueError, UnicodeDecodeError):
            return self._err(400, "请求格式不对")
        if not isinstance(data, dict):
            return self._err(400, "请求格式不对")

        if path == "/api/settings":
            with _lock:
                s = load_settings()
                if data.get("clear_key"):
                    s["api_key"] = ""
                key = data.get("api_key")
                if isinstance(key, str) and key.strip():
                    s["api_key"] = key.strip()
                if data.get("endpoint") in ENDPOINTS:
                    s["endpoint"] = data["endpoint"]
                model = data.get("model")
                if isinstance(model, str) and model.strip():
                    s["model"] = model.strip()[:100]
                _write_json(SETTINGS_FILE, s, private=True)
            return self._send(200, {"ok": True, **public_settings(s)})

        if path == "/api/history":
            rec = data.get("record")
            if not isinstance(rec, dict):
                return self._err(400, "缺少记录内容")
            rec["id"] = uuid.uuid4().hex
            rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with _lock:
                items = load_history()
                items.insert(0, rec)
                del items[MAX_HISTORY:]
                _write_json(HISTORY_FILE, items)
            return self._send(200, {"ok": True, "item": rec})

        if path == "/api/ai":
            payload = data.get("data")
            if not isinstance(payload, dict):
                return self._err(400, "缺少核算数据")
            try:
                text = call_ai(payload)
            except AIError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "text": text})

        self._err(404, "not found")

    def do_DELETE(self):
        if not self._host_ok():
            return self._err(403, "forbidden")
        path = self.path.split("?", 1)[0]
        prefix = "/api/history/"
        if not path.startswith(prefix):
            return self._err(404, "not found")
        rid = path[len(prefix):]
        with _lock:
            items = load_history()
            left = [r for r in items if r.get("id") != rid]
            if len(left) == len(items):
                return self._err(404, "记录不存在")
            _write_json(HISTORY_FILE, left)
        self._send(200, {"ok": True})


def make_server():
    try:
        return ThreadingHTTPServer((HOST, PREFERRED_PORT), Handler)
    except OSError:
        return ThreadingHTTPServer((HOST, 0), Handler)  # 由系统分配空闲端口


def main():
    if sys.version_info < (3, 9):
        print("需要 Python 3.9 或更高版本。")
        sys.exit(1)
    httpd = make_server()
    url = "http://%s:%d/" % (HOST, httpd.server_address[1])
    print("准绳 · 利润核算 已启动：" + url)
    print("数据保存在：" + str(DATA_DIR))
    print("关闭此窗口或按 Ctrl+C 即可停止。")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
