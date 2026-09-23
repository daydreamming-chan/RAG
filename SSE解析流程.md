# SSE 流式解析流程

前端入口：`static/js/app.js` 的 `sendMessage()`；后端入口：`app.py` 的 `chat_stream()` + `generate()`。

```
HTTP Response Body（字节流）
        │
        ▼
reader .read()──► { done, value }，value 是 Uint8Array
             │
             ▼
    TextDecoder.decode(value, {stream: true}) ──► 文本片段
             │                                    （stream:true 让被切开的多字节汉字能拼回来）
             ▼
    buffer += 文本片段；lines = buffer.split("\n")
             │                          │
             │                          └─ lines.pop() ──► 存回 buffer，最后一行可能只到一半
             ▼
    逐行处理（lines）
             │
             ▼
    检查"data: " 前缀
             │             │
             │        去掉前缀(slice(6))──► 空行 或 "[DONE]" ？──是──► 跳过
             │                                        │
             │                                        否
             │                                        ▼
             │                                  JSON.parse() → data
             │                                        │
             │                                  按 data.type 分发：
             │                                    "content" → 追加到 botDiv
             │                                    "error"   → 显示错误
             │                                    "meta"    → 忽略（只带检索命中数）
             │                                    "title"   → 就地更新侧边栏/顶部标题
             │
    回到reader .read()（循环）
```

## 一、后端产生事件的顺序

```
meta → content × N → [DONE] → title
```

| type | payload | 谁产生 | 前端处理 |
| --- | --- | --- | --- |
| `meta` | `retrieved_count` | `_stream_chat()` 调 LLM 之前 | 不渲染，只统计检索命中数 |
| `content` | `text` | `_stream_chat()` 每收到一个 token | 首次到达时清掉"思考中..."占位，之后 `botDiv.textContent += text` |
| `error` | `message` | 上面两条的 `except` | 追加 `[错误: xxx]` |
| `[DONE]` | —— | `_stream_chat()` 循环结束 | 只是哨兵，直接 `continue`，不解析 |
| `title` | `session_id`、`title` | `generate()` 写库之后 | `applyTitle()` 更新侧边栏和顶部标题（重命名功能也走同一条路） |

`meta` / `content` / `error` 在 `_stream_chat()` 里发，`title` 在 `generate()` 里发——因为标题要等本轮对话写进会话文件才算数。

## 二、四个容易踩的坑

1. **`buffer = lines.pop()` 不能省**
   SSE 一条消息以 `\n\n` 结尾，但网络分片不会按这个边界切。`split("\n")` 出来的最后一段很可能是一条被截断的 JSON，直接 `JSON.parse()` 会抛异常、丢内容。留到下一轮 `read()` 补全再解析。

2. **`decode(value, { stream: true })` 的 `stream: true` 不能省**
   一个 UTF-8 汉字占 3 字节，可能正好被切在两个 chunk 之间。不加 `stream: true` 会解出 `�`。

3. **不能用 `[DONE]` 当作"整条流结束"来收尾 UI**
   `title` 事件在 `[DONE]` **之后**才发。前端的循环出口是 `reader.read()` 返回 `done: true`，不是 `[DONE]`；`finally` 只负责恢复按钮和去掉占位 id。

4. **`slice(6)` 是按 `"data: "` 的 6 个字符算的**
   SSE 还允许 `data:`（带不带空格都合法），本项目格式统一由后端拼 `f"data: {payload}\n\n"`，所以前端配套写 `startsWith("data: ")` + `slice(6)`。以后若后端改格式，两边必须一起改。

## 三、浏览器侧的辅助手段

- **`AbortController`**：`state.streamingAbort` 挂载 `signal`，需要中断时 `abort()`，`reader.read()` 会抛 `AbortError`，走 `catch` 分支提示"(已终止回答)"。
- **`started` 标志**：区分"一个 token 都还没到"和"已经出过字"。前者要清掉占位文案"思考中..."，后者不能清（否则会把已渲染的内容擦掉）。
- **请求头 `X-Accel-Buffering: no`**（后端设的）：让 Nginx / 反向代理别缓冲，否则流会被攒成一大块再吐出，失去打字机效果。
- **`Cache-Control: no-store`**（`app.py` 的 `after_request` 统一加）：避免浏览器缓存 `/api/` 响应，拿到旧的会话列表或标题。
