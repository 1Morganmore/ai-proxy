# 🛡️ 국가 반사기(보이스피싱 예방) AI 리버스 프록시 서비스 (fanzha-ai-proxy)

상하이 시 공안국이 개발한 "국가 반사기 AI(国家反诈AI)" 지능형 어시스턴트를 표준 **OpenAI 호환 API 인터페이스**(`/v1/chat/completions`)로 변환해 주는 고성능 리버스 프록시 서비스입니다.

스트리밍(SSE) 출력과 비스트리밍 응답을 완벽히 지원하며, **NextChat**, **OneAPI / New API**, **LobeChat**, **Codex CLI**, **Hermes** 등 주요 AI 클라이언트 및 개발 프레임워크에 매끄럽게 바로 연동할 수 있습니다.

---

## 🌟 프로젝트 특징

- ⚡ **표준 OpenAI API**: `/v1/chat/completions` 및 `/v1/models` 규격을 완벽 지원합니다.
- 🌊 **스트리밍 SSE 응답**: 첫 패킷의 `role` 전달, 증분 delta 전송, `finish_reason: stop` 종료 플래그를 정상 지원하도록 수정 및 구현했습니다.
- 🗝️ **유연한 인증 관리**: 요청 헤더의 `Authorization: Bearer <token>`을 통한 동적 전달은 물론, 환경 변수를 통한 전역 설정도 지원합니다.
- 🔄 **자동 갱신 지원**: Access Token + Refresh Token 갱신 메커니즘이 내장되어 있어 세션 상태를 오랫동안 유지할 수 있습니다(최장 약 90일간 재로그인 불필요).
- 🧩 **멀티모달 및 프롬프트 호환**: 복잡한 `messages` 구조 및 다중 레이어 배열 형태의 content를 자동으로 파싱하고 처리합니다.

---

## 🔑 토큰(Token) 추출 가이드

> 팁: "국가 반사기 AI" 백엔드는 JWT 기반으로 신원을 인증합니다. 프록시 서비스가 인터페이스에 접근하려면 Access Token이 필요하며, Refresh Token을 설정하면 장기간 자동 갱신을 지원할 수 있습니다.

### 💡 웹 사이트를 통해 토큰을 추출하는 법
가장 간단한 방법 (PC 웹 브라우저 이용 - 앱 설치 불필요)
1. 브라우저로 [웹사이트](https://xzfzznt.gaj.sh.gov.cn/) 접속 및 로그인
2. `F12` 개발자 도구 -> `Application` 탭 -> `Local storage` 클릭
3. `user` 키값 안의 `accessToken`과 `refreshToken` 복사

<details>
<summary><b>(접기/펼치기) 📦 기존 토큰 추출 가이드 (스마트폰 앱 기반 방식)</b></summary>

> 다음은 원본이 안내했던 방식입니다. 본질적으로 앱을 스마트폰에 설치하도록 안내하고 있으나, IMEI등의 개인정보를 공유하게 되는 앱을 설치 하는 것 자체가 불필요하다고 판단하여 기본적으로 보여지지 않게 하였습니다. 

### 방법 1: ADB를 통한 로컬 데이터베이스 추출

해당 앱은 Uni-App 정적 패키징 아키텍처를 채택하고 있어, 로그인 상태가 모바일 앱의 비공개 SQLite 데이터베이스에 영구 저장됩니다.

1. **디버깅 활성화**: 스마트폰에서 개발자 옵션을 켜고 USB 디버깅을 활성화한 후, 데이터 케이블로 컴퓨터와 연결합니다(터미널에서 `adb devices`를 실행하여 기기 연결 확인).
2. **데이터베이스 경로 찾기**: 앱의 로컬 SQLite 데이터베이스 경로는 `/data/data/uni.app.UNIAD10B08/databases/DCStorage`입니다.
3. **데이터베이스 파일 추출 및 쿼리**:
   ```bash
   # 데이터베이스 파일을 SD 카드로 복사 후 컴퓨터로 가져오기
   adb shell "su -c 'cp /data/data/uni.app.UNIAD10B08/databases/DCStorage /sdcard/DCStorage'"
   adb pull /sdcard/DCStorage ./DCStorage

   # sqlite3를 사용하여 user 레코드 쿼리
   sqlite3 ./DCStorage "SELECT value FROM DC_AD10B08_storage WHERE key='user';"
   ```
4. **필드 추출**: 반환된 JSON 내용을 복호화/파싱하여 `accessToken`(Access Token)과 `refreshToken`(Refresh Token)을 찾습니다.

---

### 방법 2: Chrome Webview 디버깅 활용 (루팅 불필요)

1. 스마트폰을 컴퓨터에 연결하고 USB 디버깅을 활성화합니다.
2. 스마트폰에서 "국가 반사기 AI" 앱을 열고 AI 대화 화면으로 들어갑니다.
3. 컴퓨터에서 Chrome 브라우저를 열고 다음 주소로 접속합니다:
   ```text
   chrome://inspect/#devices
   ```
4. 페이지 목록에서 `uni.app.UNIAD10B08`에 해당하는 Webview 대상을 찾아 **inspect**를 클릭합니다.
5. 팝업으로 나타난 개발자 도구에서 **Network(네트워크)** 탭으로 전환합니다.
6. 앱에서 아무 메시지나 전송하고 캡처되는 네트워크 요청을 확인합니다.
7. 경로가 `/api/ai/create_session` 또는 `/api/ai/chat` 형태인 요청을 클릭한 후, **Request Headers**에서 다음을 찾습니다:
   ```text
   Authorization: Bearer eyJhbGciOiJIUzI1...
   ```
8. `Bearer ` 뒤의 문자열을 복사합니다. 이것이 바로 Access Token입니다.

---

### 방법 3: 패킷 캡처 툴 사용 (Fiddler / Charles / Reqable / HTTPCanary)

1. 패킷 캡처 도구를 실행하고 대상 도메인 필터를 설정합니다: `xzfzznt.gaj.sh.gov.cn`
2. AI 대화를 트리거하여 HTTP POST 요청을 가로챕니다.
3. Header의 `Authorization` 필드 값을 복사합니다.

</details>

## 🚀 빠른 시작

### 1. 의존성 설치

환경 요구 사항: Python 3.9 이상

```bash
git clone https://github.com/maakdan/fanzha-ai-proxy1.git
cd fanzha-ai-proxy
pip install -r requirements.txt
```

### 2. 환경 변수 설정

환경 변수 템플릿 복사:

```bash
cp .env.example .env
```

`.env` 파일을 편집하거나 터미널에서 환경 변수를 직접 내보냅니다(export):

**PowerShell (Windows)**:
```powershell
$env:FANZHA_ACCESS_TOKEN="당신의_Access_Token"
$env:FANZHA_REFRESH_TOKEN="당신의_Refresh_Token" # 선택 사항
```

**Bash / Zsh (Linux / macOS)**:
```bash
export FANZHA_ACCESS_TOKEN="당신의_Access_Token"
export FANZHA_REFRESH_TOKEN="당신의_Refresh_Token" # 선택 사항
```

### 3. 서비스 실행

```bash
python main.py
```

서비스는 기본적으로 `http://127.0.0.1:8088`에서 실행됩니다.

기본적으로 클라이언트의 `role: system` 프롬프트는 업스트림으로 **전달되지 않습니다**. 일반적인 챗 인터페이스에서 주입되는 `You are a helpful assistant.`와 같은 문구를 국가 반사기 모델이 탈옥 시도로 인식하여 "답변 범위를 벗어났습니다"라는 응답을 내놓기 때문입니다. 만약 전달이 필요한 경우, `FORWARD_SYSTEM_PROMPT=true`로 설정한 후 서비스를 재시작하십시오.

서버는 평탄화된 프롬프트 끝에 내장 지시문 `반드시 한국어로만 답변해 주세요. 다른 언어를 섞지 마세요.` 를 한 줄 자동으로 추가합니다. 업스트림(국가 반사기 AI)이 한국어 질문에도 중국어로 답하는 기본 동작을 바로잡기 위한 것이며, 이 지시문은 4,000자 상한을 넘기지 않도록 상한을 적용하기 전에 그 예산 안에서 자리를 확보합니다. 문구를 바꾸려면 `KOREAN_ONLY_INSTRUCTION`(또는 `config.json`의 `korean_only_instruction`)에 다른 텍스트를 넣고, 추가를 끄려면 값을 비운 뒤 서비스를 재시작하십시오.

---

## 💻 클라이언트 호출 예시

### cURL

```bash
curl http://127.0.0.1:8088/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 당신의_Access_Token" \
  -d '{
    "model": "国家反诈AI",
    "messages": [
      {"role": "user", "content": "검경 등 사법기관을 사칭해 송금을 요구하는 전화를 받았을 때 어떻게 대처해야 하나요?"}
    ],
    "stream": true
  }'
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8088/v1",
    api_key="당신의_Access_Token"  # 서버 측 환경 변수에 이미 설정되어 있다면 아무 문자열이나 입력해도 됩니다.
)

response = client.chat.completions.create(
    model="国家反诈AI",
    messages=[{"role": "user", "content": "흔히 발생하는 전기통신금융사기(보이스피싱) 수법에 대해 간단히 설명해 주세요."}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 📄 면책 조항

- 본 프로젝트는 순수 기술 교류, 학술 연구 및 개인 학습 검증용으로만 사용되며, 불법적인 용도로 사용해서는 안 됩니다.
- 본 프로젝트는 공식 앱과 어떠한 제휴나 소속 관계도 없습니다.
- 본 포크는 특정 상용/공공 시스템의 상업적 이용 및 서비스 약관 위반 행위를 지지하지 않습니다.
- 본 포크는 https://github.com/lfzk550/fanzha-ai-proxy 를 원류로 하며, [lfzk550(Louis)](https://github.com/lfzk550) 에게 2차 저작물을 제외한 모든 저작권이 있습니다. (All Rights Reserved)
