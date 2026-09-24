"""OpenAI 호환 가짜 서버.

실제 로컬 모델 서버(Ollama, LM Studio, vLLM, llama.cpp)가 쓰는 것과 같은
/v1/models, /v1/chat/completions 형식으로 대본 LLM의 답을 돌려준다.
OpenAICompatProvider가 진짜 HTTP로 붙는 경로를 검증하는 데 쓴다.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, Request

from novel_factory.llm.base import Message


def build_app(novelist, *, fail_first: int = 0, embedder=None) -> FastAPI:
    """fail_first만큼 첫 요청을 503으로 돌려 재시도 경로를 태운다.

    embedder를 주면 /v1/embeddings도 연다. 응답 순서는 일부러 뒤집어서, 클라이언트가
    index로 다시 정렬하는지 확인한다.
    """
    app = FastAPI()
    state = {"failures_left": fail_first, "requests": []}
    app.state.fake = state

    @app.get("/v1/models")
    def models() -> dict[str, object]:
        return {"object": "list", "data": [{"id": "fake-novelist", "object": "model"}]}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        state["requests"].append(body)
        if embedder is None:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "no embedding model"}, status_code=404)
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        vectors = embedder.embed(inputs)
        data = [
            {"object": "embedding", "index": i, "embedding": v}
            for i, v in enumerate(vectors)
        ]
        return {"object": "list", "model": body.get("model"), "data": data[::-1]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        state["requests"].append(body)
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "busy"}, status_code=503)
        messages = [Message(m["role"], m["content"]) for m in body["messages"]]
        completion = novelist.complete(
            messages,
            temperature=body.get("temperature"),
            max_tokens=body.get("max_tokens"),
            json_mode=bool(body.get("response_format")),
        )
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": completion.text},
                    "finish_reason": completion.finish_reason or "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    return app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(app: FastAPI):
    """앱을 백그라운드 스레드에서 띄우고 base_url을 준다."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
