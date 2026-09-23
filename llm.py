import os
from openai import OpenAI

import config


"""
懒加载： get_client() 直到第一次真正调用 API 时才初始化客户端。这样即使 .env 没配 key，
import llm 也不会崩——错误推迟到真正要联网的那一步才报，给用户留出配置空间
base_url ：DeepSeek 提供 OpenAI 兼容接口， openai 库只需换这一个地址即可复用
stream=True ：开启流式。 chat_stream 是个生成器函数， yield 逐段吐出模型生成的内容，实
现"打字机"效果
"""
_client = None

def get_client():
    """懒加载 OpenAI 客户端。只在真正调用时才初始化。"""
    global _client
    if _client is None:
        key = config.DEEPSEEK_API_KEY
        if not key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未配置，请先创建 .env 文件并填入你的 API Key"
            )   
        _client = OpenAI(api_key=key,base_url=config.DEEPSEEK_BASE_URL)
    return _client
def chat_stream(messages):
    """流式对话：逐 token 返回生成内容（生成器函数）。"""
    stream =get_client().chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        stream=True,
        temperature=0.7,
        max_tokens=2048,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content