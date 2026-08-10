"""
벡터DB(Chroma) 색인/검색 모듈 (MVP)

두 종류의 컬렉션을 만든다:
1. 법령 컬렉션 - regulations 테이블의 각 조항을 문장으로 풀어서 임베딩 (세션당 1회, 재사용)
2. 문서 컬렉션 - 업로드된 인허가서류를 청크로 쪼개 임베딩 (파일 업로드마다 새로 생성)

챗봇이 후속 질문을 받으면, 질문 자체를 임베딩해 두 컬렉션에서 관련 있는
조각(top-k)만 검색해 LLM 프롬프트에 넣는다 — 문서 전체를 매번 통째로
넣지 않아도 되므로 수백 페이지짜리 서류로 확장 가능한 구조.
"""

import os
import uuid
from typing import Optional

import chromadb
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"


def _client(api_key: Optional[str] = None) -> OpenAI:
    return OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))


def embed_texts(texts: list[str], api_key: Optional[str] = None) -> list[list[float]]:
    if not texts:
        return []
    resp = _client(api_key).embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 100) -> list[str]:
    """단순 글자수 기준 슬라이딩 윈도우 청크 분할 (한글 기준 대략 300~400자 문단 단위)."""
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == n:
            break
        start = end - overlap
    return chunks


def get_chroma_client():
    """세션 내에서만 유지되는 인메모리 벡터DB. 앱 재시작 시 초기화됨(데모 목적)."""
    return chromadb.EphemeralClient()


def build_document_collection(chroma_client, filename: str, text: str, api_key: Optional[str] = None):
    name = "doc_" + uuid.uuid4().hex[:12]
    collection = chroma_client.create_collection(name)

    chunks = chunk_text(text)
    if not chunks:
        return collection

    embeddings = embed_texts(chunks, api_key)
    ids = [f"{name}_{i}" for i in range(len(chunks))]
    metadatas = [{"filename": filename, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    return collection


def build_regulation_collection(chroma_client, regulations: list[dict], api_key: Optional[str] = None):
    collection = chroma_client.create_collection("regulations_" + uuid.uuid4().hex[:8])
    if not regulations:
        return collection

    texts = []
    for reg in regulations:
        texts.append(
            f"[{reg['law_name']} {reg.get('article', '')}] {reg['item_name']} 기준: "
            f"{reg['comparison']} {reg['limit_value']}{reg['unit']}. {reg.get('note', '')}"
        )
    embeddings = embed_texts(texts, api_key)
    ids = [f"reg_{i}" for i in range(len(texts))]
    metadatas = [{"item_name": r["item_name"]} for r in regulations]
    collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    return collection


def retrieve(collection, query: str, api_key: Optional[str] = None, k: int = 4) -> list[str]:
    """질문과 가장 관련 있는 청크 k개를 반환. 컬렉션이 비어있으면 빈 리스트."""
    if collection is None or collection.count() == 0:
        return []
    query_embedding = embed_texts([query], api_key)[0]
    results = collection.query(query_embeddings=[query_embedding], n_results=min(k, collection.count()))
    return results.get("documents", [[]])[0]
