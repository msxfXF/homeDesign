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
import subprocess
import tempfile
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
    "wan2.7-image-pro",
    "wan2.7-image",
    "qwen-image-2.0-pro",
    "qwen-image-2.0",
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


def call_wan(prompt, image_path, model, ref_data_url=None):
    """调用图像编辑模型，返回结果图片 URL。

    有参考图时：图1 = 当前画面，图2 = 参考图，提示词可用"图1/图2"引用。
    """
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    content = [{"image": "data:image/jpeg;base64," + b64}]
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


def download_and_fit(url, out_path):
    """下载结果图并转成 1500x1500 jpg（与原始面贴图一致）。"""
    tmp = tempfile.mktemp(suffix=".img")
    try:
        urllib.request.urlretrieve(url, tmp)
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-z", "1500", "1500", tmp, "--out", out_path],
            capture_output=True)
        if r.returncode != 0 or not os.path.exists(out_path):
            os.replace(tmp, out_path)  # sips 失败就用原图
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


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
                body = self.read_body()
                folder, face = body.get("folder", ""), body.get("face", "")
                edits = read_json(EDITS_PATH, {})
                entry = edits.get(folder, {}).get(face)
                if not entry:
                    return self.send_json({"error": "没有该面的编辑记录"}, 404)
                entry["active"] = body.get("file")  # None = 恢复原图
                write_json(EDITS_PATH, edits)
                return self.send_json({"ok": True})

            if path == "/api/edit":
                return self.handle_edit()

            return self.send_json({"error": "unknown api"}, 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    def handle_edit(self):
        if not API_KEY:
            return self.send_json({"error": "未配置 DASHSCOPE_API_KEY"}, 400)
        body = self.read_body()
        folder = body.get("folder", "")
        face = body.get("face", "")
        prompt = (body.get("prompt") or "").strip()
        model = body.get("model") or MODELS[0]
        ref = body.get("ref")
        if face not in FACES or not prompt or not re.fullmatch(r"[\w\-一-鿿]+", folder):
            return self.send_json({"error": "参数不合法"}, 400)
        if model not in MODELS:
            return self.send_json({"error": "不支持的模型: " + str(model)}, 400)
        if ref and not (isinstance(ref, str) and ref.startswith("data:image/")):
            return self.send_json({"error": "参考图格式不合法"}, 400)
        src = os.path.join(ROOT, "assets", "scenes", folder, face + ".jpg")
        if not os.path.exists(src):
            return self.send_json({"error": "场景图不存在"}, 404)

        # 如果该面已有生效的编辑版本，以它为参考图，支持叠加编辑
        edits = read_json(EDITS_PATH, {})
        active = edits.get(folder, {}).get(face, {}).get("active")
        if active and os.path.exists(os.path.join(ROOT, active)):
            src = os.path.join(ROOT, active)

        url = call_wan(prompt, src, model, ref)

        out_dir = os.path.join(ROOT, "assets", "edits", folder)
        os.makedirs(out_dir, exist_ok=True)
        fname = "%s_%d.jpg" % (face, int(time.time()))
        download_and_fit(url, os.path.join(out_dir, fname))

        rel = "assets/edits/%s/%s" % (folder, fname)
        entry = edits.setdefault(folder, {}).setdefault(face, {"active": None, "versions": []})
        entry["versions"].append({
            "file": rel, "prompt": prompt, "model": model,
            "time": time.strftime("%Y-%m-%d %H:%M"),
        })
        entry["active"] = rel
        write_json(EDITS_PATH, edits)
        return self.send_json({"ok": True, "file": rel})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print("天峰903 全景服务器: http://localhost:%d" % PORT)
    print("AI 改图: %s" % ("已启用 (wan2.7-image)" if API_KEY else "未启用 — 请设置 DASHSCOPE_API_KEY"))
    ThreadingHTTPServer(("", PORT), partial(Handler, directory=ROOT)).serve_forever()
