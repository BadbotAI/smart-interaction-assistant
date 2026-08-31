"""确定性文本向量：字符 unigram + bigram 哈希到 192 维，L2 归一化。

私有化演示场景下不依赖外部 embedding API。生产环境替换为
gte-Qwen2-7B-instruct 等模型时仅需保持 embed() 接口不变。
"""
import hashlib
import math

DIM = 192


def _hash(token: str) -> int:
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "big")


def embed(text: str) -> list:
    vec = [0.0] * DIM
    text = (text or "").strip()
    if not text:
        return vec
    grams = []
    chars = list(text)
    grams.extend(chars)
    grams.extend(a + b for a, b in zip(chars, chars[1:]))
    for g in grams:
        h = _hash(g)
        idx = h % DIM
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))
