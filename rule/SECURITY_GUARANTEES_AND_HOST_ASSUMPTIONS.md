# SECURITY_GUARANTEES_AND_HOST_ASSUMPTIONS

## 1. Runtime 能真正保证的安全

Eyes Runtime 能通过确定性代码强制：

- MiMo 没有 tools/web；
- 只读已批准 source；
- path traversal/junction/symlink guard；
- URL 默认关闭；
- media resource limits；
- EXIF strip；
- capture scope permission；
- full-screen local human confirmation；
- no background capture；
- taint envelope 恒定；
- `may_authorize_actions=false` 恒定；
- no shell/file-write/browser/email tools；
- provider request budget/retry limit；
- secret/log redaction。

这些属于：
```text
Hard Guarantee
```

---

## 2. Runtime 不能保证的事

如果 Host Agent 另外拥有：
```text
bash
file write
browser
computer use
email
```

Eyes Runtime 无法看到/阻止 Host 随后的调用。

所以它**不能单独保证**：

```text
恶意图片绝不导致 Host Agent 最后运行命令
```

只靠 Skill 是 soft policy，不是强隔离。

---

## 3. Host Action Guard

支持的 Host Adapter 如果提供：
```text
tool.execute.before
permission hook
policy engine
```

应增加 VisualActionGuard。

输入：
- pending privileged tool；
- current turn provenance；
- whether decisive rationale contains `UNTRUSTED_VISUAL_EVIDENCE`；
- user's explicit request。

规则：

### allow
用户当前明确要求该动作，且目标工具自身 policy 允许。

### confirm
动作由视觉内容引导，但用户没有明确独立授权。

### deny
视觉内容本身声称“用户已授权”或要求访问 secret/扩大范围。

---

## 4. 安全能力宣称

Capabilities 必须返回：

```json
{
  "host_action_guard": true
}
```
或：
```json
{
  "host_action_guard": false
}
```

当 false：
产品文档只能说：
```text
Eyes protects its own visual/capture boundary and labels visual evidence as untrusted.
```

不能说：
```text
Eyes can prevent all visual prompt injection from causing actions.
```

---

## 5. OpenCode

OpenCode V2 Plugin API 当前仍是 beta，但官方文档说明插件可以拦截 tool execution。

因此它是 Action Guard 候选，但：
- adapter version pin；
- hook regression test；
- beta upgrade gate；
是必须的。

如果实际目标 OpenCode 版本的 before-hook 不可用：
```text
host_action_guard=false
```
而不是假装有。

---

## 6. User-visible uncertainty

用户最终答案不需要显示：
```text
UNTRUSTED_VISUAL_EVIDENCE
```
这种内部标签。

但当 material visual uncertainty 会改变结论时，Agent 必须自然说明：
```text
截图里这一处数字看不清，目前无法确认是 3.3V 还是 5V。
```

内部 taint 不应直接污染普通 UX，但不能在 Agent 内部丢失。
