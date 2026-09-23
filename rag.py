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

def index_document (filename , content):
    """分块→ TF-IDF 向量化→ 存入DocStore。返回chunk 数量"""
    chunks_texts = split_text (content)
    if not chunks_texts:
        return 0
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
    storage .add_document (filename , chunks)
    # 用全量语料重建向量器，保证所有文档的向量在同一词汇空间
    rebuild_vectorizer()
    _re_encode_all ()
    return len (chunks)

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