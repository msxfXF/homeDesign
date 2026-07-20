/**
 * 天峰903 全景查看器 Cloudflare Worker。
 *
 * 静态资源 + 批注/AI 改图 API。与本地 server.py 提供相同的接口：
 * 数据存 KV（键：annotations / edits / img:<folder>/<file>），仓库里已提交的
 * 编辑图作为静态资源兜底。写操作需要 x-edit-token 口令（EDIT_TOKEN secret）。
 * 图片一律字节流透传，避免在 Worker 里做 base64 编解码消耗 CPU。
 */

const WAN_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
const ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations";
const DASHSCOPE_MODELS = ["qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro", "wan2.7-image"];
const ARK_MODELS = ["doubao-seedream-5-0-260128", "doubao-seedream-5-0-pro-260628"];
const FACES = new Set(["f", "b", "l", "r", "u", "d"]);
const FOLDER_RE = /^[\w\-一-鿿]+$/;
const CONSTRAINT = "。除上述要求外，严格保持画面其他所有内容不变：构图、视角、家具、材质、光线和色调都与原图一致。";

const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status,
  headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});

const modelsFor = env =>
  [...(env.ARK_API_KEY ? ARK_MODELS : []), ...(env.DASHSCOPE_API_KEY ? DASHSCOPE_MODELS : [])];

const authed = (request, env) =>
  !env.EDIT_TOKEN || request.headers.get("x-edit-token") === env.EDIT_TOKEN;

async function getJson(env, key, staticPath, request) {
  const v = await env.PANO_KV.get(key, "json");
  if (v !== null) return v;
  try {
    const r = await env.ASSETS.fetch(new URL(staticPath, request.url));
    if (r.ok) return await r.json();
  } catch { /* 静态兜底失败按空处理 */ }
  return {};
}

async function callWan(env, prompt, model, image, ref) {
  const content = [{ image }];
  if (ref) content.push({ image: ref });
  content.push({ text: prompt });
  const parameters = { n: 1, watermark: false };
  if (model.startsWith("qwen")) parameters.size = "2048*2048";
  const r = await fetch(WAN_URL, {
    method: "POST",
    headers: { Authorization: "Bearer " + env.DASHSCOPE_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      input: { messages: [{ role: "user", content }] },
      parameters,
    }),
  });
  const res = await r.json();
  if (!res.output) throw new Error(res.message || JSON.stringify(res).slice(0, 300));
  for (const item of res.output.choices[0].message.content) if (item.image) return item.image;
  throw new Error("响应中没有图片");
}

async function callArk(env, prompt, model, image, ref) {
  const images = ref ? [image, ref] : image;
  const r = await fetch(ARK_URL, {
    method: "POST",
    headers: { Authorization: "Bearer " + env.ARK_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({
      model, prompt, image: images,
      size: "2048x2048", response_format: "url", watermark: false,
    }),
  });
  const res = await r.json();
  if (!r.ok) throw new Error(res.error?.message?.slice(0, 400) || "HTTP " + r.status);
  if (!res.data?.length) throw new Error("响应中没有图片");
  return res.data[0].url;
}

async function handleApi(request, env, path) {
  const url = new URL(request.url);

  if (path === "/api/health")
    return json({ ok: true, ai: modelsFor(env).length > 0, models: modelsFor(env) });

  if (path === "/api/annotations" && request.method === "GET")
    return json(await getJson(env, "annotations", "/assets/annotations.json", request));

  if (path === "/api/edits" && request.method === "GET")
    return json(await getJson(env, "edits", "/assets/edits.json", request));

  if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
  if (!authed(request, env)) return json({ error: "需要编辑口令" }, 401);

  if (path === "/api/annotations") {
    const data = await request.json();
    if (typeof data !== "object" || Array.isArray(data)) return json({ error: "annotations 必须是对象" }, 400);
    await env.PANO_KV.put("annotations", JSON.stringify(data));
    return json({ ok: true });
  }

  if (path === "/api/edit-view") {
    const b = await request.json();
    const prompt = (b.prompt || "").trim();
    const model = b.model || modelsFor(env)[0];
    if (!prompt || !(b.image || "").startsWith("data:image/")) return json({ error: "参数不合法" }, 400);
    if (!modelsFor(env).includes(model)) return json({ error: "不支持的模型: " + model }, 400);
    if (b.ref && !b.ref.startsWith("data:image/")) return json({ error: "参考图格式不合法" }, 400);
    const call = ARK_MODELS.includes(model) ? callArk : callWan;
    const resultUrl = await call(env, prompt + CONSTRAINT, model, b.image, b.ref);
    const img = await fetch(resultUrl);
    if (!img.ok) throw new Error("结果下载失败 " + img.status);
    return new Response(img.body, {
      headers: {
        "Content-Type": img.headers.get("Content-Type") || "image/jpeg",
        "Cache-Control": "no-store",
      },
    });
  }

  if (path === "/api/edit-image") {
    const folder = url.searchParams.get("folder") || "";
    const face = url.searchParams.get("face") || "";
    const op = url.searchParams.get("op") || "";
    if (!FOLDER_RE.test(folder) || !FACES.has(face) || !/^\d+$/.test(op))
      return json({ error: "参数不合法" }, 400);
    const rel = `assets/edits/${folder}/${face}_${op}.jpg`;
    await env.PANO_KV.put("img:" + `${folder}/${face}_${op}.jpg`, request.body);
    return json({ ok: true, file: rel });
  }

  if (path === "/api/edit-apply") {
    const b = await request.json();
    const { folder, op } = b;
    const faces = b.faces || [];
    if (!FOLDER_RE.test(folder || "") || !/^\d+$/.test(op || "") ||
        !faces.length || !faces.every(f => FACES.has(f)))
      return json({ error: "参数不合法" }, 400);
    const edits = await getJson(env, "edits", "/assets/edits.json", request);
    const now = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace("T", " ");
    for (const face of faces) {
      const rel = `assets/edits/${folder}/${face}_${op}.jpg`;
      const entry = (edits[folder] = edits[folder] || {});
      const fe = (entry[face] = entry[face] || { active: null, versions: [] });
      fe.versions.push({ file: rel, prompt: (b.prompt || "").trim(), model: b.model || "", time: now, op });
      fe.active = rel;
    }
    await env.PANO_KV.put("edits", JSON.stringify(edits));
    return json({ ok: true, op, faces });
  }

  if (path === "/api/edits/active") {
    const b = await request.json();
    const edits = await getJson(env, "edits", "/assets/edits.json", request);
    const affected = [];
    for (const [face, entry] of Object.entries(edits[b.folder] || {})) {
      const ver = entry.versions.find(v => (v.op || v.file) === b.op);
      if (!ver) continue;
      entry.active = b.on ? ver.file : null;
      affected.push(face);
    }
    if (!affected.length) return json({ error: "没有该编辑记录" }, 404);
    await env.PANO_KV.put("edits", JSON.stringify(edits));
    return json({ ok: true, faces: affected });
  }

  if (path === "/api/edits/delete") {
    const b = await request.json();
    const edits = await getJson(env, "edits", "/assets/edits.json", request);
    const removed = [];
    for (const face of Object.keys(edits[b.folder] || {})) {
      const entry = edits[b.folder][face];
      const keep = [];
      for (const v of entry.versions) {
        if ((v.op || v.file) === b.op) {
          removed.push(v.file);
          if (entry.active === v.file) entry.active = null;
        } else keep.push(v);
      }
      entry.versions = keep;
      if (!keep.length) delete edits[b.folder][face];
    }
    if (edits[b.folder] && !Object.keys(edits[b.folder]).length) delete edits[b.folder];
    if (!removed.length) return json({ error: "没有该编辑记录" }, 404);
    await env.PANO_KV.put("edits", JSON.stringify(edits));
    for (const rel of removed)
      if (rel.startsWith("assets/edits/"))
        await env.PANO_KV.delete("img:" + rel.slice("assets/edits/".length));
    return json({ ok: true, removed: removed.length });
  }

  return json({ error: "unknown api" }, 404);
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path.startsWith("/api/"))
      return handleApi(request, env, path).catch(e => json({ error: String(e.message || e) }, 500));

    // 新生成的编辑图存在 KV，仓库里已提交的走静态资源
    if (path.startsWith("/assets/edits/")) {
      const key = "img:" + decodeURIComponent(path.slice("/assets/edits/".length));
      const val = await env.PANO_KV.get(key, "arrayBuffer");
      if (val) return new Response(val, {
        headers: { "Content-Type": "image/jpeg", "Cache-Control": "no-store" },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
