#!/usr/bin/env python3
"""天峰903 全景查看器本地服务器。

静态文件 + 批注保存 + AI 改图代理（阿里云 wan2.7-image）。
API key 从环境变量 DASHSCOPE_API_KEY 或 .env 文件读取，绝不下发到前端。

用法: python3 server.py  →  http://localhost:8931
"""
import base64
import json
import os
import re
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8931
ANN_PATH = os.path.join(ROOT, "assets", "annotations.json")
EDITS_PATH = os.path.join(ROOT, "assets", "edits.json")
WAN_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
FACES = set("fblrud")
MODELS = [
    "qwen-image-2.0-pro",
    "qwen-image-2.0",
    "wan2.7-image-pro",
    "wan2.7-image",
]


def load_api_key():
    key = os.environ.get("DASHSCOPE_API_KEY")
    if key:
        return key.strip()
    env_file = os.path.join(ROOT, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


API_KEY = load_api_key()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def call_wan(prompt, image_path, model, ref_data_url=None, image_data_url=None):
    """调用图像编辑模型，返回结果图片 URL。

    有参考图时：图1 = 当前画面，图2 = 参考图，提示词可用"图1/图2"引用。
    """
    if image_data_url is None:
        with open(image_path, "rb") as f:
            image_data_url = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    content = [{"image": image_data_url}]
    if ref_data_url:
        content.append({"image": ref_data_url})
    content.append({"text": prompt})
    body = json.dumps({
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"n": 1, "watermark": False},
    }).encode()
    req = urllib.request.Request(WAN_URL, data=body, headers={
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.load(r)
    if "output" not in res:
        raise RuntimeError(res.get("message", json.dumps(res)[:300]))
    for item in res["output"]["choices"][0]["message"]["content"]:
        if "image" in item:
            return item["image"]
    raise RuntimeError("响应中没有图片: " + json.dumps(res)[:300])


def download_as_data_url(url):
    """下载结果图，返回 data URL（交给前端做重投影）。"""
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())


def save_data_url(data_url, out_path):
    """把前端传来的 data URL 图片落盘。"""
    b64 = data_url.split(",", 1)[1]
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))


class Handler(SimpleHTTPRequestHandler):

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path.split("?")[0] == "/api/health":
            return self.send_json({"ok": True, "ai": bool(API_KEY), "models": MODELS})
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/annotations":
                data = self.read_body()
                if not isinstance(data, dict):
                    return self.send_json({"error": "annotations 必须是对象"}, 400)
                write_json(ANN_PATH, data)
                return self.send_json({"ok": True})

            if path == "/api/edits/active":
                return self.handle_active()

            if path == "/api/edits/delete":
                return self.handle_delete()

            if path == "/api/edit-view":
                return self.handle_edit_view()

            if path == "/api/edit-apply":
                return self.handle_edit_apply()

            return self.send_json({"error": "unknown api"}, 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    def handle_active(self):
        """切换生效版本。op 模式整组切换（一次视野编辑覆盖的所有面）。

        body: {folder, op, on: true/false}
        """
        body = self.read_body()
        folder, op = body.get("folder", ""), body.get("op", "")
        on = bool(body.get("on"))
        edits = read_json(EDITS_PATH, {})
        affected = []
        for face, entry in edits.get(folder, {}).items():
            # 旧版单面记录没有 op 字段，用文件路径当组键
            ver = next((v for v in entry["versions"]
                        if (v.get("op") or v["file"]) == op), None)
            if not ver:
                continue
            entry["active"] = ver["file"] if on else None
            affected.append(face)
        if not affected:
            return self.send_json({"error": "没有该编辑记录"}, 404)
        write_json(EDITS_PATH, edits)
        return self.send_json({"ok": True, "faces": affected})

    def handle_delete(self):
        """删除一组编辑版本（含图片文件）。body: {folder, op}"""
        body = self.read_body()
        folder, op = body.get("folder", ""), body.get("op", "")
        edits = read_json(EDITS_PATH, {})
        removed = []
        for face in list(edits.get(folder, {})):
            entry = edits[folder][face]
            keep = []
            for v in entry["versions"]:
                if (v.get("op") or v["file"]) == op:
                    removed.append(v["file"])
                    if entry.get("active") == v["file"]:
                        entry["active"] = None
                else:
                    keep.append(v)
            entry["versions"] = keep
            if not keep:
                del edits[folder][face]
        if folder in edits and not edits[folder]:
            del edits[folder]
        if not removed:
            return self.send_json({"error": "没有该编辑记录"}, 404)
        write_json(EDITS_PATH, edits)
        for rel in removed:
            p = os.path.normpath(os.path.join(ROOT, rel))
            if p.startswith(os.path.join(ROOT, "assets", "edits")) and os.path.exists(p):
                os.remove(p)
        return self.send_json({"ok": True, "removed": len(removed)})

    def handle_edit_view(self):
        """AI 编辑当前视野截图，返回结果图（不落盘，前端做重投影）。"""
        if not API_KEY:
            return self.send_json({"error": "未配置 DASHSCOPE_API_KEY"}, 400)
        body = self.read_body()
        prompt = (body.get("prompt") or "").strip()
        model = body.get("model") or MODELS[0]
        image = body.get("image") or ""
        ref = body.get("ref")
        if not prompt or not image.startswith("data:image/"):
            return self.send_json({"error": "参数不合法"}, 400)
        if model not in MODELS:
            return self.send_json({"error": "不支持的模型: " + str(model)}, 400)
        if ref and not (isinstance(ref, str) and ref.startswith("data:image/")):
            return self.send_json({"error": "参考图格式不合法"}, 400)

        full_prompt = prompt + "。除上述要求外，严格保持画面其他所有内容不变：构图、视角、家具、材质、光线和色调都与原图一致。"
        url = call_wan(full_prompt, None, model, ref, image_data_url=image)
        return self.send_json({"ok": True, "image": download_as_data_url(url)})

    def handle_edit_apply(self):
        """保存前端重投影后的各面贴图，注册为一组编辑版本。

        body: {folder, prompt, model, faces: {face: dataURL}}
        """
        body = self.read_body()
        folder = body.get("folder", "")
        prompt = (body.get("prompt") or "").strip()
        model = body.get("model") or ""
        faces = body.get("faces") or {}
        if not re.fullmatch(r"[\w\-一-鿿]+", folder) or not faces:
            return self.send_json({"error": "参数不合法"}, 400)
        if not all(f in FACES and isinstance(d, str) and d.startswith("data:image/")
                   for f, d in faces.items()):
            return self.send_json({"error": "面数据不合法"}, 400)

        edits = read_json(EDITS_PATH, {})
        op = str(int(time.time()))
        out_dir = os.path.join(ROOT, "assets", "edits", folder)
        os.makedirs(out_dir, exist_ok=True)
        now = time.strftime("%Y-%m-%d %H:%M")
        for face, data_url in faces.items():
            fname = "%s_%s.jpg" % (face, op)
            save_data_url(data_url, os.path.join(out_dir, fname))
            rel = "assets/edits/%s/%s" % (folder, fname)
            entry = edits.setdefault(folder, {}).setdefault(
                face, {"active": None, "versions": []})
            entry["versions"].append({
                "file": rel, "prompt": prompt, "model": model,
                "time": now, "op": op,
            })
            entry["active"] = rel
        write_json(EDITS_PATH, edits)
        return self.send_json({"ok": True, "op": op, "faces": list(faces)})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print("天峰903 全景服务器: http://localhost:%d" % PORT)
    print("AI 改图: %s" % ("已启用 (wan2.7-image)" if API_KEY else "未启用 — 请设置 DASHSCOPE_API_KEY"))
    ThreadingHTTPServer(("", PORT), partial(Handler, directory=ROOT)).serve_forever()
