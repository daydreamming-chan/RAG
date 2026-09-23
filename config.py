import os
from dotenv import load_dotenv

# 指定 .env 文件路径，确保无论从哪个目录启动都能找到
load_dotenv(os.path.join(os.path.dirname(__file__),".env"))

"""
load_dotenv(...) ：传入了 config.py 所在目录的绝对路径，而不是靠当前工作目录去找。这是最
常见的新手坑——从不同目录运行 python app.py 时，不加路径参数会导致读不到 .env
os.getenv(key, default) ：每个配置都有默认值， .env 不写也能跑
DATA_DIR ：所有运行时数据（文档索引、会话记录）都存在项目内的 data/ 目录，不用外部数
据库
os.makedirs(..., exist_ok=True) ：首次启动自动创建目录
"""
DEEPSEEK_API_KEY =os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL =os.getenv("DEEPSEEK_BASE_URL","https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL","deepseek-v4-flash")

CHUNK_SIZE =int(os.getenv("CHUNK_SIZE","400"))
CHUNK_OVERLAP =int(os.getenv("CHUNK_OVERLAP", "100"))
RETRIEVAL_TOP_K =int(os.getenv("RETRIEVAL_TOP_K", "4"))

DATA_DIR = os.path.join(os.path.dirname(__file__),"data")
DOCUMENTS_DIR = os.path.join(DATA_DIR,"documents")

os.makedirs(DOCUMENTS_DIR, exist_ok=True)