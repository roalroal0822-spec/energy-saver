"""시험용 가짜 모델 서버 (OpenAI 호환 /v1/chat/completions, Ollama /api/chat).

실제 모델이 아니다. 앱의 모델 연결 경로(요청 형식, JSON 해석, 숫자 대조 검증)가 동작하는지 확인하려고
출력 습관이 다른 두 가지 응답 방식을 흉내 낸다.
  mock-a : 사실에 있는 숫자만 쓰고 JSON 을 깔끔하게 돌려준다.
  mock-b : ```json 코드블록·설명 문장을 섞고, 답에 사실에 없는 숫자를 일부러 넣는다(앱이 걸러야 함).
실행: python tools/mock_llm.py --port 18080
"""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def bill_fields(user):
    text = user.split("\n\n", 1)[-1]
    out = {}
    m = re.search(r"([\d,]+)\s*kWh", text.split("최대부하")[-1])
    if "kwh" in user.split("\n\n")[0]:
        m = re.search(r"(?:당월|합계|총)[^\n]*?([\d,]+)\s*kWh", text)
        if m:
            out["kwh"] = m.group(1).replace(",", "")
    if "total" in user.split("\n\n")[0]:
        m = re.search(r"(?:청구|납부)[^\n]*?([\d,]+)\s*원", text)
        if m:
            out["total"] = m.group(1).replace(",", "")
    if "ym" in user.split("\n\n")[0]:
        m = re.search(r"(20\d{2})\D{1,3}(\d{1,2})", text)
        if m:
            out["ym"] = f"{m.group(1)}-{int(m.group(2)):02d}"
    if "fac" in user.split("\n\n")[0]:
        m = re.search(r"(?:고객명|사용장소)\s*(.+)", text)
        if m:
            out["fac"] = m.group(1).strip()
    return out


def reply(model, messages):
    user = "\n".join(m.get("content", "") for m in messages if m.get("role") in ("user", "system"))
    bad = model.endswith("b")
    if "찾을 항목" in user:
        obj = bill_fields(user)
        s = json.dumps(obj, ensure_ascii=False)
        return f"고지서에서 찾은 값입니다.\n```json\n{s}\n```" if bad else s
    facts = re.findall(r"^- (.+)$", user, re.M)
    text = " ".join(facts[:3]) or "확인할 사실이 없습니다."
    if bad:
        text += " 이 시설은 지난해보다 약 37.5% 비효율적인 것으로 보입니다."
    return text


class H(BaseHTTPRequestHandler):
    def _send(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send({"data": [{"id": "mock-a"}, {"id": "mock-b"}]})
        return self._send({"models": [{"name": "mock-a"}, {"name": "mock-b"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        out = reply(req.get("model", "mock-a"), req.get("messages", []))
        if self.path.endswith("/api/chat"):
            return self._send({"message": {"role": "assistant", "content": out}, "done": True, "done_reason": "stop"})
        return self._send({"choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    a = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
