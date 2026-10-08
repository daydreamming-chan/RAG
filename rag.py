import hashlib
import pickle
import os
import numpy as np
from sklearn .feature_extraction .text import TfidfVectorizer
from sklearn .metrics .pairwise import cosine_similarity
from config import CHUNK_SIZE , CHUNK_OVERLAP , RETRIEVAL_TOP_K , DATA_DIR 
import storage

VECTORIZER_PATH = os .path .join (DATA_DIR ,"vectorizer.pkl")

_vectorizer = None

def get_vectorizer():
    """获取全局TF-IDF 向量器。首次调用从磁盘加载，没有则创建新的"""
    global _vectorizer
    if _vectorizer is not None :
        return _vectorizer
    if os.path.exists (VECTORIZER_PATH):
        with open (VECTORIZER_PATH , "rb") as f :
            _vectorizer = pickle .load (f)
    else:
        _vectorizer = TfidfVectorizer (
            analyzer="char_wb", ngram_range=(2 , 4), max_features=10000
        )
    return _vectorizer

def split_text (text , chunk_size=CHUNK_SIZE , overlap=CHUNK_OVERLAP):
    """滑动窗口分块：每chunk_size 字符一个块，相邻块重叠overlap 字符"""
    text = text .strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len (text): 
        end = start + chunk_size 
        chunk = text [start:end]
        chunks .append (chunk)
        start = end - overlap 
        if start >= len (text):
            break
    return chunks

def index_document (filename , content , content_hash=None):
    """分块→ TF-IDF 向量化→ 存入DocStore。返回chunk 数量"""
    return _index_document (filename , content , content_hash)[1]


def _index_document (filename , content , content_hash=None):
    """真正的写库动作，返回 (doc_id, chunk 数量)。

    只负责"写"，不做任何查重——跳过/覆盖的决策统一放在 ingest_document() 里，
    避免一处判重、一处写入，两边规则打架。
    """
    if content_hash is None :
        content_hash = hash_content (content)
    chunks_texts = split_text (content)
    if not chunks_texts:
        return None , 0
    # 用所有新chunk 更新词汇表
    vec = get_vectorizer()
    vec.fit(chunks_texts)
    embeddings = vec .transform (chunks_texts)

    # 组装chunk 对象
    chunks = []
    for i , (text , emb) in enumerate (zip(chunks_texts , embeddings)):
        chunks .append({
            "text": text ,
            "embedding": emb .toarray()[0].tolist(),
        })
    doc_id = storage .add_document (filename , chunks , content_hash)
    # 用全量语料重建向量器，保证所有文档的向量在同一词汇空间
    rebuild_vectorizer()
    _re_encode_all ()
    return doc_id , len (chunks)

#旧文本新文本融合重新训练
def rebuild_vectorizer():
    """在全量chunk 上重新训练向量器"""
    global _vectorizer
    all_chunks = storage .get_all_chunks()
    texts = [c["text"] for c in all_chunks]
    _vectorizer = TfidfVectorizer (
        analyzer="char_wb", ngram_range=(2,4), max_features=10000
    )
    _vectorizer.fit(texts)
    with open (VECTORIZER_PATH , "wb") as f:
        pickle .dump (_vectorizer , f)
    return _vectorizer

def _re_encode_all ():
       """用新的全局向量器重新编码所有chunk"""
       all_chunks = storage .get_all_chunks()
       if not all_chunks:
            return
       vec = get_vectorizer()
       texts = [c["text"] for c in all_chunks]
       matrices = vec .transform (texts)
       ds = storage .get_docstore()
       for i , row in enumerate (matrices):
            ds ["chunks"][i]["embedding"] =row .toarray()[0].tolist()
       storage.save_docstore(ds)


# ── 上传查重 / 同名覆盖 ──

def hash_content (content):
    """算正文的 sha256，作为"内容是否已存在"的唯一依据。

    先做归一化（去掉 BOM、CRLF/CR 统一成 LF、掐掉首尾空白）：
    同一个文件在 Windows 和 Linux 上换行符不同、编辑器可能加 BOM，
    不归一化会被算成"内容不同"，白白多存一份。
    """
    text = (
        content
        .lstrip("\ufeff")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )
    return hashlib .sha256 (text .encode ("utf-8")).hexdigest()


def _stored_text (doc_id):
    """把库里已存的 chunk 反拼回正文（给老数据补算 hash 用，不需要用户重传）。

    split_text 的起点是等距推进的，除首块外每块开头都有 CHUNK_OVERLAP 个字符
    和上一块重叠，所以首块全留、后续块切掉前 CHUNK_OVERLAP 个字符即可精确还原。
    """
    texts = [
        c .get("text","")
        for c in sorted (
            (c for c in storage .get_all_chunks() if c .get("doc_id") == doc_id),
            key=lambda c: c .get("chunk_index",0),
        )
    ]
    if not texts:
        return ""
    return texts [0] + "".join (t [CHUNK_OVERLAP:] for t in texts [1:])


def _backfill_missing_hashes ():
    """给没有 content_hash 的老文档补算并写回（幂等，算过就跳过）。

    不补的话老文档在内容查重里永远匹配不上，同内容的文件重传会被当成新文档。
    """
    for doc in storage .list_documents ():
        if doc .get("content_hash"):
            continue
        h = hash_content (_stored_text (doc ["id"]))
        if h:
            storage .set_document_hash (doc ["id"], h)


def ingest_document (filename , content):
    """上传入库的唯一入口：先查重，再决定 跳过 / 覆盖 / 新增。

    规则（先看内容、再看文件名）：
      1) 内容已在库 + 文件名相同 → 跳过（情况1：重复上传同一个文件）
      2) 内容已在库 + 文件名不同 → 跳过，并回报库里的文件名（情况2：同一份内容换个名字传）
      3) 文件名已在库 + 内容不同 → 删旧增新，算更新不算重复（情况3：新版覆盖旧版）
      4) 都不命中 → 新增

    说明：1/2 优先于 3。万一同名旧文档的内容恰好和库里另一份文档一字不差，
    宁可不动也不覆盖——否则库里会出现两份完全相同的正文。
    另外文件名相同但内容也相同的情况会被 1 拦下，不会走到 3 去白删白建。

    返回 {"action": "skipped" | "updated" | "created" | "rejected", ...}
    """
    if not content .strip():
        return {
            "action": "rejected",
            "reason": "empty_content",
            "filename": filename,
            "chunk_count": 0,
            "message": f"《{filename}》内容为空，未入库。",
        }

    content_hash = hash_content (content)
    _backfill_missing_hashes ()

    # 情况1 / 情况2：内容已存在
    same_hash = storage .find_documents_by_hash (content_hash)
    if same_hash:
        # 命中多条时优先报同名那条，提示更贴合用户当前操作
        hit = next ((d for d in same_hash if d .get("filename") == filename), same_hash [0])
        if hit .get("filename") == filename:
            return {
                "action": "skipped",
                "reason": "same_name_same_content",
                "filename": filename,
                "existing_filename": filename,
                "doc_id": hit ["id"],
                "chunk_count": hit .get("chunk_count", 0),
                "message": f"《{filename}》已在库中（文件名与内容都相同），本次跳过。",
            }
        return {
            "action": "skipped",
            "reason": "same_content",
            "filename": filename,
            "existing_filename": hit .get("filename"),
            "doc_id": hit ["id"],
            "chunk_count": hit .get("chunk_count", 0),
            "message": (
                f"内容已在库中，库中文件名为《{hit .get('filename')}》，"
                f"本次跳过（未新建《{filename}》）。"
            ),
        }

    # 情况3：同名但内容不同 → 先删后增
    same_name = storage .find_documents_by_filename (filename)
    if same_name:
        old_chunks = sum (d .get("chunk_count", 0) for d in same_name)
        old_ids = storage .remove_documents_by_filename (filename)
        doc_id , count = _index_document (filename , content , content_hash)
        return {
            "action": "updated",
            "reason": "same_name_new_content",
            "filename": filename,
            "existing_filename": filename,
            "doc_id": doc_id,
            "chunk_count": count,
            "replaced_doc_ids": old_ids,
            "message": (
                f"《{filename}》内容已更新，旧版已覆盖"
                f"（{old_chunks} 块 → {count} 块）。"
            ),
        }

    # 情况4：全新文档
    doc_id , count = _index_document (filename , content , content_hash)
    return {
        "action": "created",
        "reason": "new_document",
        "filename": filename,
        "doc_id": doc_id,
        "chunk_count": count,
        "message": f"《{filename}》已入库（{count} 块）。",
    }


def retrieve(query , top_k=RETRIEVAL_TOP_K):
    """将查询转为TF-IDF 向量，与所有chunk 算余弦相似度，返回top_k"""
    all_chunks = storage .get_all_chunks()
    if not all_chunks:
        return [], []
    
    vec = get_vectorizer()
    query_vec = vec .transform([query])
    chunk_vecs = np .array([c["embedding"] for c in all_chunks])

    # 余弦相似度（TF-IDF 向量已L2 归一化，直接点积即可）
    sims = cosine_similarity (query_vec .toarray(), chunk_vecs)[0]
    ranked = sorted (
        zip (all_chunks , sims),
        key=lambda x: x [1],
        reverse=True ,
    )
    top = ranked[:top_k]
    retrieved_chunks = []
    retrieved_scores = []
    for chunk , score in top:
        retrieved_chunks.append (chunk)
        retrieved_scores.append (float(score))

    return retrieved_chunks , retrieved_scores