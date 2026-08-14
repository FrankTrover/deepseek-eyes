# MVP_SCOPE — 第一版真正要做到什么

## MVP-A：核心可用

必须：
1. 用户/测试程序显式注册一张图片；
2. 获取 source_ref；
3. text Agent 通过 MCP 调 `deepseek_eyes_observe`；
4. Runtime 用 MiMo v2.5 Token Plan 看图；
5. 返回 tainted structured evidence；
6. exact cache/single-flight；
7. no capture；
8. no Control Center。

这验证：
```text
Eyes Core 是真的
```

---

## MVP-B：真实 Agent 可用

增加：
1. Attachment Ingress Adapter；
2. Vision Skill；
3. 首发 Coding Host；
4. 用户拖/贴图；
5. text DeepSeek 自动拿到 source_ref marker；
6. Agent 按 Skill 调 Eyes。

这验证：
```text
DeepSeek 真正长眼睛
```

---

## V1 Product

再增加：
- Windows interactive region capture；
- Control Center；
- Credential/Permission UX；
- integration wizard；
- Action Guard（若 Host 支持）。

---

## 不把 Capture 作为 Core MVP 阻塞项

用户附图路径先跑通。

Capture 是额外媒体入口，不应该阻碍核心视觉链路开发。
