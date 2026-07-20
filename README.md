# homeDesign · 天峰903 全景漫游

自家装修方案「天峰903」的 VR 全景查看器，共 13 个场景（客餐厅、玄关、厨房、卧室、卫生间、衣帽间、阳台等）。

**在线访问：** https://msxfxf.github.io/homeDesign/

## 操作

- 鼠标拖拽 / 单指滑动：环视
- 滚轮 / 双指捏合：缩放
- 键盘 ← →：切换场景
- 底部缩略图：点击跳转场景
- 📱 按钮（手机）：陀螺仪模式
- 场景内白色圆形热点：走到相邻房间

## 热点编辑

打开 `?edit` 模式标注场景之间的跳转热点：

```
https://msxfxf.github.io/homeDesign/?edit
```

1. 点击画面中要放热点的位置（一般点地面）
2. 在右侧面板选择目标场景，点「放置热点」
3. 编辑结果暂存在浏览器 localStorage，点「导出 hotspots.json」下载
4. 用下载的文件替换 `assets/hotspots.json`，提交推送即生效

## URL 参数

- `?scene=05_厨房` 指定初始场景
- `?yaw=45&pitch=10` 指定初始视角（度）
- `?edit` 开启热点编辑模式

## 批注 & AI 改图（本地模式）

本地用 `python3 server.py` 启动（替代 http.server），打开 http://localhost:8931 后解锁两个功能：

- **✍️ 批注**：点击画面任意位置写想法，自动保存到 `assets/annotations.json`；💬 侧边栏列出全部批注，点击跳转到对应场景和视角。推送后线上也能看（只读）。
- **🎨 AI 改图**：对当前朝向的立方体面输入修改描述（如"把沙发换成米色布艺沙发"），调用阿里云 wan2.7-image 生成并直接替换全景贴图，支持多版本切换/恢复原图，可叠加编辑。结果存在 `assets/edits/`。

AI 改图需要 API key：在项目根目录建 `.env` 文件写入 `DASHSCOPE_API_KEY=sk-xxx`（已 gitignore，不会提交），或设置同名环境变量。key 只在本地服务端使用，不会出现在前端代码里。

## 技术

- Three.js（本地 vendor，无 CDN 依赖）立方体全景渲染：每个场景 6 张 1500×1500 面贴图（f/b/l/r/u/d）贴在立方体内表面，相机居中
- 纯静态，无构建步骤；场景清单在 `assets/scenes.json`，热点在 `assets/hotspots.json`
- 本地预览：`python3 server.py` 后打开 http://localhost:8931（纯静态浏览也可用 `python3 -m http.server`）
