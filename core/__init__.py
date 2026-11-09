"""공통 기반: 문서 읽기, 검색, 모델 연결, 저장, 내보내기, 웹 서버."""

# 시각은 한국 시간으로 기록한다(Render 같은 서버는 기본이 UTC라 기록·기상 자료 기준 시각이 9시간 어긋남).
# 윈도우는 PC 시간대를 그대로 쓴다(tzset 없음).
import os as _os
import time as _time
if hasattr(_time, "tzset"):
    _os.environ["TZ"] = _os.environ.get("APP_TZ", "KST-9")
    _time.tzset()
