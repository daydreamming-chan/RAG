from flask import Flask, Response, request, jsonify, send_from_directory


import json
import os
import re
from datetime import datetime
import config
import llm
import rag
import storage


app=Flask(__name__ , static_folder="static" , static_url_path="")


"""
Flask(__name__, static_folder="static", static_url_path="") ：把 static/ 目录挂到
根路径，这样/css/style.css 、 /js/app.js 会自动被托管，不需要手写路由

/api/sessions 和 /api/documents ：先返回空列表占位。虽然功能还没实现，但路由的形态已经
定下来，前端后面按这个接口写，不会返工

debug=True ：改代码自动重启，方便开发
"""
@app.after_request
def _no_cache_api(resp):
    """API 响应一律不缓存：否则浏览器可能拿着旧的会话列表/标题不放"""
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory("static","index.html")



# ── 会话接口 ──

@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    #会话功能
    return jsonify(storage.list_sessions())


# —— 会话窗口 ——

@app.route("/api/sessions", methods=["POST"])
def new_session():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip() or None
    session = storage.create_session(title)
    return jsonify(session)


# —— 获取会话 ——

@app.route("/api/sessions/<session_id>",methods=["GET"])
def get_session(session_id):
    s = storage.get_session(session_id)
    if not s:
        return jsonify({"error": "session not found"}), 404
    return jsonify(s)


# —— 删除会话 ——

@app.route("/api/sessions/<session_id>",methods=["DELETE"])
def delete_session(session_id):
    ok = storage.delete_session(session_id)
    if not ok:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"deleted": True})


# —— 重命名会话 ——

@app.route("/api/sessions/<session_id>", methods=["PATCH", "PUT"])
def rename_session(session_id):
    data = request.get_json(silent=True) or {}
    raw = data.get("title")
    title = str(raw).strip() if raw is not None else ""

    if not title:
        return jsonify({"error": "empty title"}), 400

    session = storage.rename_session(session_id, title[:60])
    if not session:
        return jsonify({"error": "session not found"}), 404

    #只回传前端要用到的字段
    return jsonify({"id": session["id"], "title": session["title"]})


# ── 聊天接口（SSE 流式）──

def _build_system_prompt (context_texts):
    if context_texts:
        ctx = "\n---\n".join (context_texts)
        return (
            "你是一个知识库助手。请仅根据以下资料回答用户的问题，"
            "如果资料中没有答案，请如实说'知识库中暂无相关内容'。\n"
            "重要:本对话早期的回答可能基于已经删除或过时的旧资料,"
            "当历史回答与下方参考资料冲突时,必须以参考资料为准,"
            "不要为了保持与历史一致而沿用旧答案。\n\n"
            f"【参考资料】\n{ctx}"
        )
    return "你是一个知识库助手。知识库当前为空，请告知用户先上传文档。"


# def _stream_chat(history_messages, query):
#     """组装消息并流式调用 DeepSeek，以 SSE 格式yield。"""
#     messages = [{"role": "system", "content": "你是助手，请用简洁的中文回答问题。"}]
#     for m in history_messages:
#         messages.append({"role": m["role"], "content": m["content"]})
#     messages.append({"role": "user","content": query})

#     try:
#         for token in llm.chat_stream(messages):
#             payload = json.dumps({"type":"content","text":token},ensure_ascii=False)
#             yield f"data: {payload}\n\n"
#         yield "data: [DONE]\n\n"
#     except Exception as e:
#         payload = json.dumps({"type":"error", "message": str(e)},ensure_ascii=False)
#         yield f"data: {payload}\n\n"


def _stream_chat (history_messages , query , top_k=None):
    if top_k is None :
        top_k = config .RETRIEVAL_TOP_K
    # 检索相关文档片段
    retrieved , scores = rag .retrieve (query , top_k=top_k)
    context_texts = [c["text"] for c in retrieved]
    system = _build_system_prompt (context_texts)

    # 组装消息：system + 历史+ 当前问题
    messages = [{"role": "system","content": system}]
    for m in history_messages:
        messages .append({"role": m ["role"],"content": m ["content"]})
    messages .append({"role": "user","content": query})

    try:
        stream =llm .get_client().chat .completions .create (
            model=config .DEEPSEEK_MODEL , 
            messages=messages ,
            stream=True ,
            temperature=0.7 ,
            max_tokens=2048 ,
        )

        # 先发检索元信息
        yield f"data: {json .dumps({'type':'meta', 'retrieved_count':len(retrieved)})}\n\n"

        for chunk in stream:
                delta = chunk .choices [0].delta
                if delta .content:
                    text = delta .content
                    payload =json .dumps({"type": "content", "text":text}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

        yield "data: [DONE]\n\n"

    except Exception as e:
            payload = json .dumps({"type":"error", "message": str (e)},ensure_ascii=False)
            yield f"data: {payload}\n\n"


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    data = request.get_json(force=True)
    query = data.get("message", "").strip()
    session_id = data.get("session_id","").strip()

    if not query:
        return jsonify({"error": "empty message"}), 400

    # 没传 session_id 或 session 不存在 → 自动创建
    if not session_id:
        s = storage.create_session()
        session_id = s["id"]
    elif not storage.get_session(session_id):
        s = storage.create_session()
        session_id = s["id"]


    def generate():
        session = storage.get_session(session_id)
        #历史会话不存在返回空列表
        history = session.get("messages",[]) if session else []

        #完整回复
        full_answer = ""
        for sse_line in _stream_chat(history, query):
            if sse_line.strip():
                try:
                    raw = sse_line.replace("data: ", "").strip()
                    #当前内容不为空或结束行
                    if raw and raw != "[DONE]":
                        d = json.loads(raw)
                        if d.get("type") == "content":
                            full_answer += d.get("text", "")
                except Exception:
                    pass
            yield sse_line

        # 流结束后把本轮对话写入会话
        if session:
            session["messages"].append({"role": "user", "content": query})
            #机器回复
            session["messages"].append({"role":"assistant", "content": full_answer})

            # 自动用第一条消息设置会话标题（手动重命名过的标题不覆盖）
            if not session.get("title_locked") and (
                not session.get("title") or session["title"].startswith("会话")
            ):
                #前30字符作为标题，超过用...
                session["title"] = query[:30] + ("..." if len(query) > 30 else "")

            storage.save_session(session)

            # 把最新标题推给前端：前端就地更新侧边栏，不需要重新拉列表或刷新页面
            yield f"data: {json.dumps({'type': 'title', 'session_id': session_id, 'title': session['title']}, ensure_ascii=False)}\n\n"
          
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/api/documents", methods=["GET"])
def list_documents():
    return jsonify(storage .list_documents())


# ── 原始文档备份 ──

#Windows 文件名非法字符（含路径分隔符和控制字符）
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_backup_name(filename):
    """把上传的文件名收敛成安全的纯文件名：去掉路径成分 + 替换非法字符。

    必须过滤：filename 由客户端提供，如果直接拼进路径，
    ../../x.md 这类名字会写到备份目录之外。
    """
    name = os.path.basename(filename.replace("\\", "/"))
    name = _INVALID_NAME_CHARS.sub("_", name).strip(" .")
    return name or f"upload_{datetime.now():%Y%m%d%H%M%S}.md"


def _save_backup(filename, raw):
    """把上传的原始字节写进 DOCUMENTS_DIR，返回落盘的（安全）文件名。同名直接覆盖。"""
    path = os.path.join(config.DOCUMENTS_DIR, _safe_backup_name(filename))
    with open(path, "wb") as f:
        f.write(raw)
    return os.path.basename(path)


@app .route ("/api/documents/upload", methods=["POST"])
def upload_document():
    if "file" not in request .files:
        return jsonify({"error": "no file"}), 400
    f = request .files ["file"]
    if not f .filename:
        return jsonify({"error": "no filename"}), 400

    #先拿原始字节：备份要原样保存，只有建索引才需要解码成文本
    raw = f.read()
    content = raw.decode("utf-8", errors="replace")

    #索引和备份统一用规范名：否则 "C:\a\x.md" 和 "x.md" 会被当成两个不同文件，
    #查重（按文件名）就失效了
    name = _safe_backup_name(f.filename)

    #查重 + 入库：内容相同跳过、同名不同内容覆盖，决策全在 rag.ingest_document 里
    result = rag .ingest_document (name , content)
    if result ["action"] == "rejected":
        return jsonify({"error": result ["message"]}), 400

    resp = {
        "filename": result ["filename"],
        "action": result ["action"],        #created / updated / skipped
        "reason": result ["reason"],
        "message": result ["message"],
        "chunk_count": result ["chunk_count"],
    }
    #情况2：内容相同但文件名不同，把库里的文件名回给前端
    existing = result .get("existing_filename")
    if existing and existing != result ["filename"]:
        resp ["existing_filename"] = existing
    #情况3：被覆盖掉的旧文档 id，方便前端/排查用
    if result .get("replaced_doc_ids"):
        resp ["replaced_doc_ids"] = result ["replaced_doc_ids"]

    #只有真写进索引（新增/更新）才落备份；跳过的不碰备份目录，
    #否则会出现"备份里有、索引里没有"的文件
    if result ["action"] != "skipped":
        try:
            resp ["backup"] = _save_backup (name , raw)
        except Exception as e:
            #备份失败不让整次上传失败：索引已经建好，只把原因回传给前端
            resp ["backup_error"] = str (e)
    return jsonify(resp)

@app .route ("/api/documents/<doc_id>", 
methods=["DELETE"])
def delete_document (doc_id):
    if not storage .remove_document (doc_id):
        return jsonify({"error": "document not found"}), 404
    return jsonify({"deleted": doc_id})



if __name__ == "__main__":
 app.run(host="0.0.0.0", port=8000,debug=True)