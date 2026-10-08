import json
import os
import uuid
from datetime import datetime
from config import DATA_DIR

SESSIONS_DIR = os.path.join(DATA_DIR,"sessions")
DOCSTORE_PATH = os.path.join(DATA_DIR,"docstore.json")
os.makedirs(SESSIONS_DIR, exist_ok=True)


"""
一个会话一个文件：增删改查只需操作对应文件
消息格式： messages 是列表，每项 {role: "user"/"assistant", content: "..."} ，
兼容 OpenAI 消息格式
自动标题： create_session 生成默认标题如"会话 a1b2c3d4"，后续发第一条消息时会用消息内容
替

链路：
上传文档 → add_document(切块+算embedding) → 写入 docstore.json
提问     → get_all_chunks() → retrieve() 算余弦相似度 → top_k → 喂给 LLM
会话     → create_session / save_session / list_sessions / delete_session

"""

def _load_json(path, default=None):
    """读取 JSON 文件，不存在时返回默认值"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}

def _save_json(path, data):
    """写入 JSON 文件"""
    with open(path, "w", encoding="utf-8")as f:
        json.dump(data,f,ensure_ascii=False, indent=2)


def get_session_path(session_id):
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")

def create_session(title=""):
    """新建会话，返回会话对象，id默认生成8位"""
    session_id = str(uuid.uuid4())[:8]
    session = {
        "id": session_id,
        "title": title or f"会话{session_id}",
        #手动命名过的会话不再被首轮消息自动覆盖标题
        "title_locked": bool(title),
        "created_at":datetime.now().isoformat(),
        "messages": [],
    }
    _save_json(get_session_path(session_id),session)
    return session


def get_session(session_id):
    """读取单个会话"""
    path = get_session_path(session_id)
    if not os.path.exists(path):
        return None
    return _load_json(path)

def save_session(session):
    """保存（更新）会话"""
    _save_json(get_session_path(session["id"]),session)


def rename_session(session_id, title):
    """重命名会话，返回更新后的会话；会话不存在返回 None"""
    session = get_session(session_id)
    if not session:
        return None
    session["title"] = title
    #打上锁定标记：以后发消息不会再被问题内容覆盖
    session["title_locked"] = True
    save_session(session)
    return session


def list_sessions():
    """列出所有会话，按创建时间倒序"""
    sessions = []
    if not os.path.exists(SESSIONS_DIR):
        return sessions
    for fname in os.listdir(SESSIONS_DIR):
        if fname.endswith(".json"):
            s =_load_json(os.path.join(SESSIONS_DIR,fname))
            if s:
                sessions.append({
                    "id": s["id"],
                    "title": s["title"],
                    "created_at":s["created_at"],
                    "message_count":len(s.get("messages", [])),
                })
    sessions.sort(key=lambda x:x["created_at"], reverse=True)
    return sessions

def delete_session(session_id):
    """删除会话文件"""
    path = get_session_path(session_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False

#文档入库
def get_docstore():
    return _load_json (DOCSTORE_PATH ,{"documents": {}, "chunks": []})

def save_docstore (docstore):
    _save_json (DOCSTORE_PATH , docstore)


#第二次改：加 content_hash 做查重依据——"内容相同"由 hash 判定，"要不要覆盖"由 filename 判定
def add_document (filename , chunks , content_hash=""):
    """添加一个文档及其所有chunk。chunks 是列表，每项= {text, embedding}
    content_hash 是正文的 sha256，一起落盘，后续上传靠它判断"内容是否已在库中"
    """
    ds = get_docstore()
    doc_id = str (uuid .uuid4())[:8]
    ds ["documents"][doc_id] = {
        "filename": filename ,
        "chunk_count": len (chunks),
        "content_hash": content_hash,
        "uploaded_at": datetime.now().isoformat(),
    }
    for i , chunk in enumerate (chunks): 
        ds ["chunks"].append({
            "doc_id": doc_id ,
            "filename": filename ,
            "chunk_index": i ,
            "text": chunk ["text"],
            "embedding": chunk ["embedding"],
        })
    save_docstore(ds)
    return doc_id

def remove_document(doc_id):
    """按文档 id 删除文档及其所有chunk，返回是否删除成功"""
    ds = get_docstore()
    if doc_id not in ds["documents"]:
        return False
    ds["chunks"] = [c for c in ds["chunks"] if c.get("doc_id") != doc_id]
    ds["documents"].pop(doc_id, None)
    save_docstore(ds)
    return True

def find_documents_by_hash(content_hash):
    """按内容哈希查文档（内容查重），返回 [{"id": ..., ...}]，没有则空列表"""
    if not content_hash:
        return []
    ds = get_docstore()
    return [
        {"id": k, **v}
        for k, v in ds["documents"].items()
        if v.get("content_hash") == content_hash
    ]

def find_documents_by_filename(filename):
    """按文件名查文档（同名覆盖用），返回 [{"id": ..., ...}]"""
    ds = get_docstore()
    return [
        {"id": k, **v}
        for k, v in ds["documents"].items()
        if v.get("filename") == filename
    ]

def remove_documents_by_filename(filename):
    """按文件名删掉所有同名文档及其 chunk，返回被删的 doc_id 列表。

    用于"同名覆盖"：先删旧版再增新版。按名字删是为了兼容历史上
    同名重复入库过的数据（按 id 删只能删掉一条，会留下孤儿 chunk）。
    """
    ds = get_docstore()
    ids = [k for k, v in ds["documents"].items() if v.get("filename") == filename]
    if ids:
        idset = set(ids)
        ds["chunks"] = [c for c in ds["chunks"] if c.get("doc_id") not in idset]
        for doc_id in ids:
            ds["documents"].pop(doc_id, None)
        save_docstore(ds)
    return ids

def set_document_hash(doc_id, content_hash):
    """给单个文档补写 content_hash（老数据迁移用），成功返回 True"""
    ds = get_docstore()
    doc = ds["documents"].get(doc_id)
    if not doc or not content_hash:
        return False
    doc["content_hash"] = content_hash
    save_docstore(ds)
    return True

def list_documents():
    """列出所有已入库的文档（带 doc_id，供删除使用）"""
    ds = get_docstore()
    return [{"id": k, **v} for k, v in ds["documents"].items()]

def get_all_chunks():
    """获取全部chunk，供检索使用"""
    ds = get_docstore()
    return ds["chunks"]