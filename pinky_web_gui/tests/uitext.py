"""화면 문구 검사 도우미 (verify_*_ui.py 공용). GUI 의 고정 문구는 한국어다 (PRD 공통 원칙 7, 영문화는 철회).
약어·식별자(ACK, SIL, HH, info, manager, 파일 이름 …)는 그대로 쓰므로 검사에서 뺀 뒤 영문 단어가 남는지 본다."""
import re

ALLOWED = re.compile(r'HH|LL|info|config/[\w./-]+|alarms(\.local)?\.yaml|[\w.-]+\.(wav|mp3|ogg)|\b(ACK|SIL|HH|LL|ID|LiDAR|info|manager|operator|mngr|oper|mp3|wav|ogg|MB|HTTP|JSON|CSV|Pinky|ROBOT|CMD)\b|\{\w+\}', re.I)
WORD = re.compile(r'[A-Za-z]{4,}')


def english_left(txt):
    """고정 문구에서 허용 약어를 뺀 뒤 남은 영문 단어들 (없으면 빈 목록)."""
    return WORD.findall(ALLOWED.sub(' ', txt))
