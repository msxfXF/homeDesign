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

## 技术

- Three.js（本地 vendor，无 CDN 依赖）立方体全景渲染：每个场景 6 张 1500×1500 面贴图（f/b/l/r/u/d）贴在立方体内表面，相机居中
- 纯静态，无构建步骤；场景清单在 `assets/scenes.json`，热点在 `assets/hotspots.json`
- 本地预览：`python3 -m http.server` 后打开 http://localhost:8000
