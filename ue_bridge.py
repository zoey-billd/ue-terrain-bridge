"""最小 UE5 TCP bridge(127.0.0.1:9877).

用途:让 Gaea→Houdini→UE 地形桥接能把 `ue_import.py` 发进 UE 编辑器执行。

用法(任选其一):
1. 在 UE 的 Python 控制台执行一次:
       exec(open(r"你的项目/Content/Python/ue_bridge.py", encoding="utf-8").read())
2. 在项目的 Content/Python/init_unreal.py 末尾加:
       exec(open(r"你的项目/Content/Python/ue_bridge.py", encoding="utf-8").read())

注意:9877 是本插件的专用端口。不要改成 9876(常被别的 UE 项目占用)。
"""

import socket as _sock
import json as _json
import io
import sys
import threading as _thr

_pending = []
_lock = _thr.Lock()


def _on_tick(_dt):
    with _lock:
        if not _pending:
            return
        conn, code = _pending.pop(0)
    try:
        out = io.StringIO()
        orig = sys.stdout
        sys.stdout = out
        try:
            exec(code, {"unreal": __import__("unreal")})
        finally:
            sys.stdout = orig
        try:
            conn.sendall(_json.dumps({"result": out.getvalue()}).encode())
        except Exception:
            pass
    except Exception as exc:
        try:
            conn.sendall(_json.dumps({"message": str(exc)}).encode())
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _serve():
    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 9877))
    srv.listen(5)
    print("UE bridge listening on 127.0.0.1:9877")
    while True:
        try:
            conn, _ = srv.accept()
            msg = _json.loads(conn.recv(65536).decode())
            with _lock:
                _pending.append((conn, msg.get("code", "")))
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


_thr.Thread(target=_serve, daemon=True).start()

import unreal
unreal.register_slate_post_tick_callback(_on_tick)
