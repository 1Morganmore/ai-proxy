# ai-proxy

[English](README.md) | [한국어](README.ko.md)

An OpenAI-compatible reverse proxy for the web AI behind China's National Anti-Fraud Center service (国家反诈AI). It runs on your machine, accepts `POST /v1/chat/completions`, and relays the question to `https://xzfzznt.gaj.sh.gov.cn`, converting the upstream SSE stream back into OpenAI-style chunks.

This repository is a preservation fork of `maakdan/fanzha-ai-proxy1`. It hosts no model, provides no account, and grants no access to the upstream service. Use only credentials for an account you own and are authorized to use. The project is not affiliated with the upstream service or with OpenAI.

Everything below was read from the code on this branch on September 8, 2026 (`main.py`, 19,300 bytes, blob `f8994ec0a965af026ab6d25df41723c7ee7b65ee`, upstream revision `c10441f5`). No live end-to-end call against the upstream service was made while writing it.

## Scope

The proxy implements these routes:

| Route | Behavior |
| --- | --- |
| `POST /v1/chat/completions`, `POST /chat/completions` | Chat, with `"stream": true` and `"stream": false` |
| `GET /v1/models`, `GET /models` | Fixed, hard-coded model list |
| `GET /`, `GET /health` | `{"status": "ok"}` from the local process |
| `GET /kb/search?keyword=&locale=` | Pass-through to the upstream anti-fraud knowledge search |
| `GET /kb/term?keyword=&limit=&locale=` | Pass-through to the upstream FAQ / dictionary search |
| `GET /kb/list?type=&locale=` | Pass-through to the upstream `anti-fraud/list` catalog (see below) |

Chat is the only route that talks to the gated model. The two `/kb/*` routes read the anti-fraud knowledge base directly, so they keep working on questions the chat gate refuses. Beyond these, there are no embeddings, no `/v1/responses`, no tool calling, no image input, and no real multimodal support: text is extracted from complex `content` structures and everything else is dropped. Treat chat as a narrow shim, not a complete OpenAI API.

## Requirements

Python 3.9 or newer, Git, pip, and a valid upstream access token from your own account. The 3.9 floor comes from the code itself, which uses builtin generics such as `dict[str, Any]` in evaluated annotations. Dependencies are declared with minimum versions in [requirements.txt](requirements.txt), so there is no reproducible lockfile.

## Quick start

```bash
git clone https://github.com/1Morganmore/ai-proxy.git
cd ai-proxy
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python main.py
```

Two notes on that block. The upstream README's quick start says `cd fanzha-ai-proxy`, which matches no repository name in this lineage; cloning the URL above creates a folder named `ai-proxy`. And `main.py` looks for `.env` and `config.json` next to itself, not in your working directory, so keep the configuration files in the project folder.

`python main.py` starts uvicorn on `127.0.0.1:8088` unless you override `HOST` and `PORT`. Launching with `uvicorn main:app --host 127.0.0.1 --port 8088` works too, since the configuration is loaded at import time.

## Get your own token

The default upstream origin in [main.py](main.py) is `https://xzfzznt.gaj.sh.gov.cn/`. Open it in a browser you control, sign in with your own account, and confirm the site can answer a harmless question before touching DevTools.

1. Press **F12** (or **Ctrl+Shift+I**) on the AI site, not on GitHub.
2. Open the **Application** tab, then **Local storage**, then the site's origin entry.
3. Find the key named **`user`** and read its value. Copy **`accessToken`** into `FANZHA_ACCESS_TOKEN`, and **`refreshToken`**, if present, into `FANZHA_REFRESH_TOKEN`.
4. Paste field values only. No `Bearer ` prefix, no surrounding quotes, no braces, no line breaks. The code adds `Bearer ` itself when calling upstream, so a stored prefix would be sent twice.

If the storage entry is missing or shaped differently, use the **Network** tab instead. Enable **Preserve log**, filter by **Fetch/XHR**, send one question, then open the real request (not an `OPTIONS` preflight) for `/api/ai/create_session` or the chat call and copy the token value out of the `Authorization` request header. A refresh token is optional; never invent one or substitute a cookie or CSRF value.

Keep the credentials to yourself. Don't paste tokens into chats, issues, screenshots, HAR exports, or third-party "token checker" sites, and don't share the whole `user` object, which can carry other account data.

## Configuration

Settings resolve in this order: process environment, then `.env`, then `config.json`, then the built-in default. Non-empty values only. Restart the process after an edit; nothing is hot-reloaded.

| Environment variable | `config.json` key | Default | Notes |
| --- | --- | --- | --- |
| `FANZHA_ACCESS_TOKEN` | `access_token` | empty | Upstream JWT, without the `Bearer ` prefix |
| `FANZHA_REFRESH_TOKEN` | `refresh_token` | empty | Optional, enables auto-refresh on a 401 |
| `FANZHA_BASE_URL` | `base_url` | `https://xzfzznt.gaj.sh.gov.cn` | Your token is sent here, so don't point it at a host you don't trust |
| `HOST` | `host` | `127.0.0.1` | Read by `python main.py` |
| `PORT` | `port` | `8088` | Must parse as an integer in 1..65535 |
| `DEFAULT_MODEL` | `default_model` | `国家反诈AI` | Label used when a request omits `model` |
| `FORWARD_SYSTEM_PROMPT` | `forward_system_prompt` | `false` | `true`, `1`, `yes`, or `on` forwards client `role: system` text upstream |
| `KOREAN_ONLY_INSTRUCTION` | `korean_only_instruction` | `반드시 한국어로만 답변해 주세요. 다른 언어를 섞지 마세요.` | Answer-language instruction appended to every prompt. Set another phrase to reword it, or an empty value to stop appending (the one setting where empty is meaningful) |
| `FANZHA_REFUSAL_AS_ERROR` | `fanzha_refusal_as_error` | `true` | Map the upstream out-of-scope gate refusal to an error (see [Errors](#errors)); `false` forwards the refusal text as a normal answer |
| `STRIP_BOILERPLATE` | `strip_boilerplate` | `intent-only` | `off`, `intent-only` (drop in-band `current_intent:` blocks), or `full` (also drop the trailing 防范建议/温馨提示 closer). An unrecognised value aborts startup |

`.env` is loaded with `load_dotenv(..., override=False)`, so a variable already exported in your shell wins over the file. Watch for leftovers from an earlier session.

`config.example.json` is a template. Copy it to `config.json` only if you want that source; `.env` alone is enough. A malformed `config.json` raises at startup even when every other setting is fine. Both `.env` and `config.json` are listed in `.gitignore`, and `.venv/` is not, so don't commit either one.

## Point a client at it

| Setting | Value |
| --- | --- |
| API type | OpenAI-compatible Chat Completions |
| Base URL | `http://127.0.0.1:8088/v1` |
| API key | `sk-local` |
| Model | `fanzha-ai` |

`sk-local` is a placeholder, not a secret and not a password. The proxy runs no client-key check of its own. It substitutes a per-request credential only when the incoming `Authorization: Bearer ...` value looks like a JWT (starts with `eyJ` and contains two dots); any other string, `sk-local` included, is ignored and the configured token is used. Because nothing authenticates your client, keep the default loopback binding. If a client lives in a VM, container, or another machine, remember that loopback is not shared between them, and the fix is not to bind on `0.0.0.0`.

Health check from a second terminal:

```bash
curl http://127.0.0.1:8088/health
```

A plain non-streaming call:

```bash
curl http://127.0.0.1:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-local' \
  -d '{"model":"fanzha-ai","messages":[{"role":"user","content":"What are three warning signs of a phone scam?"}],"stream":false}'
```

Streaming:

```bash
curl -N http://127.0.0.1:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-local' \
  -d '{"model":"fanzha-ai","messages":[{"role":"user","content":"What are three warning signs of a phone scam?"}],"stream":true}'
```

Expect an answer about fraud-prevention topics, and expect it in Korean: the proxy appends a built-in Korean-only instruction to every prompt (`KOREAN_ONLY_INSTRUCTION`), without which the upstream model usually replies in Chinese. `/v1/models` returns a fixed list (`国家反诈AI`, `fanzha-ai`, `gpt-4o-mini`); none of those names selects a different upstream model. The requested label is echoed back in the response while the upstream payload sends an empty `model_name`, so a `gpt-4o-mini` label is not evidence of an OpenAI model anywhere in the chain.

## What happens to your request

Each call creates a fresh upstream session via `POST /api/ai/create_session`, so the upstream service holds no conversation state between requests. The proxy then flattens `messages` into one text prompt: turns are labeled `系统`, `用户`, and `助手`, everything after the last user message is discarded, and the whole prompt is capped at 4000 characters, with the last user turn kept and older turns trimmed to fit. Before that cap is applied, the built-in `KOREAN_ONLY_INSTRUCTION` sentence is appended on its own line, so the text that reaches upstream always ends with it and still fits inside 4000 characters. System text is dropped unless `FORWARD_SYSTEM_PROMPT` is enabled; upstream docs say injected boilerplate such as "You are a helpful assistant" can be read as a jailbreak attempt and trigger an out-of-scope refusal, which is why the default is off.

The chat call is `POST /api/ai/chat?type=0` with a JSON body containing `text`, `version: 2`, `stream: true`, `max_tokens` (client value or 2048), `temperature: "0.1"`, `using_context: false`, `answer_mode: "normal"`, an empty `files` list, and the session id as `conversation_id`. Request headers imitate a web browser: `Authorization: Bearer <token>`, `channel: web`, matching `Origin` and `Referer`, and a desktop Chrome user agent. Answer text is read from SSE events where `data.type` is `answer`. Upstream calls time out after 90 seconds, session creation and token refresh after 10.

If a 401 comes back during session creation and a refresh token is configured, the proxy posts to `/api/v1/user/token/refresh` once and retries with the new token, which is then also used for the chat request. Refreshed tokens live in memory only; they are never written to disk, so a restart falls back to whatever is in your configuration.

### The gate refusal

Upstream is a routed workflow, not a bare model. When its intent router decides a question is unrelated to telecom fraud, it answers **HTTP 200** with one fixed 40-character string, `您好，您的提问超出了我的回答范畴，如有与电信网络诈骗相关的问题，欢迎您继续咨询。`, and its router node leaks the marker `与反诈无关`. Both literals are matched in the assembled text (and, while streaming, in every prefix of it), so no refusal reaches the client as a successful answer:

- Non-streaming: `503` with `{"detail": "fanzha_out_of_domain"}`.
- Streaming: the refusal text is withheld, then one `object: "error"` event with `code: 503` and message `fanzha_out_of_domain`, then `data: [DONE]`.

That is what makes the service usable inside a fallback chain: an agent can treat `fanzha_out_of_domain` as "this provider declined, try the next one" instead of passing a polite dead end to the user. Set `FANZHA_REFUSAL_AS_ERROR=false` to get the raw refusal text back. Because matching is substring-based, an in-domain answer that happens to quote either literal is also mapped; the marker form is what the router emits, not something the answer model normally writes.

### Boilerplate in the answer

`STRIP_BOILERPLATE` decides how much wire noise is removed before the answer is returned. `intent-only` (the default) drops in-band `current_intent:` blocks, including the fenced `{"class": "..."}` JSON that follows them — the vendor frontend parses that marker out of the answer itself, which is what proves it is transport noise rather than content. `full` additionally drops a trailing closer section whose heading is a bare `防范建议`, `温馨提示`, `特别提醒`, `注意事项`, or `实用建议` (in bold, `###`, or `防范建议：<one-liner>` form), provided the whole section is at most 400 characters. A heading that only mentions one of those words inside a longer phrase stays, and an unmarked code fence is never touched. Streaming and non-streaming run through the same line-oriented filter, so the same upstream answer produces the same text either way. `off` returns upstream text verbatim.

### Knowledge lookups

The upstream service also exposes plain JSON endpoints for its anti-fraud knowledge base, and they authorise with the same bearer token the chat flow uses. The proxy forwards them server-side, so a client can query the corpus without a chat round-trip and without the gate:

```bash
curl -sG http://127.0.0.1:8088/kb/search --data-urlencode 'keyword=转账' --data-urlencode 'locale=zh-CN'
curl -sG http://127.0.0.1:8088/kb/term  --data-urlencode 'keyword=刷单' --data-urlencode 'limit=10'
```

`/kb/search` forwards to `GET /api/v1/anti-fraud/search` and returns `{"code": 200, "data": {"cases": [...], "types": [...], "news": [...]}}`; `/kb/term` forwards to `GET /api/v1/faq/search`. Both pass the upstream HTTP status and JSON body through unchanged, so a `400` from an unsupported `type` or `locale` is visible to the caller instead of being papered over. They always use the **configured** token, never a client `Authorization` header, and they reject any request whose peer address is not loopback — a second line of defence behind the default `HOST=127.0.0.1` binding.

`/kb/list` forwards to `GET /api/v1/anti-fraud/list`, whose `type` parameter is a server-side literal enum. What it is **not**: the `glossary` / `top-cases` / `law` strings you see in `/pages/read/index?type=…`. Those are uni-app route parameters from the dictionary and reader pages, and the API answers `400` (`数据验证错误: query -> type: Input should be …`) for them, as it does for numeric values like `1`. What it **is**: the value the vendor bundle itself sends — `assets/pages-fraud-type-index.*.js` calls `list("type", locale)`, i.e. `?type=type`, while `assets/news.*.js` defaults its wrapper to `?type=news`. So the honest state of this route is: implemented as a straight passthrough, defaults to `type=type`, offline-verified, and **not** confirmed against a live 200 yet, because the probe budget for this endpoint was spent on the two `400`s above. Read the `message` field of any `400` you get: it enumerates the accepted values.

## Errors

| Situation | Result |
| --- | --- |
| No configured token and no JWT-shaped Bearer header | `401` |
| Body isn't valid JSON, `messages` is empty, or nothing survives flattening | `400` |
| Session creation fails, or returns no session id | `502`, detail includes the upstream status and a body excerpt |
| Upstream chat request answers 4xx or 5xx | `502`, detail includes up to 500 characters of the upstream body |
| Upstream stream yields no answer text | Non-streaming: `502`. Streaming: one `object: "error"` event with code 502, then `data: [DONE]` |
| Upstream answer is the out-of-scope gate refusal (or carries the `与反诈无关` marker) | Non-streaming: `503` with `"detail": "fanzha_out_of_domain"`. Streaming: one `object: "error"` event with code 503 and that message, then `data: [DONE]`. Suppressed entirely by `FANZHA_REFUSAL_AS_ERROR=false` |
| `/kb/*` called from a non-loopback peer | `403` |
| `/kb/*` upstream returns a non-JSON body, or the request fails | `502` |
| Anything unexpected | `500` |

There is no canned success fallback. This revision removed the old hard-coded acknowledgment, so an empty or failed upstream response surfaces as an error rather than a polite greeting. One caveat: once a stream has started, the HTTP status is already 200, so clients must inspect the events instead of trusting the status line. Error details can carry upstream response fragments, which is useful locally and unsafe to paste publicly.

## What is not verified

- **Registration.** Whether email-only signup still exists, and whether disposable or alias addresses are accepted, is unverified. A private alias is easier to recover than a public temp inbox, but it does not override a mandatory phone or identity check.
- **Availability.** Upstream uptime and API shape are not guaranteed. A changed endpoint, payload, or auth mechanism breaks this proxy, and a web session using a different authentication flow is not automatically compatible.
- **Token lifetime.** `.env.example` repeats an upstream claim of roughly 90 days of auto-renewal. Neither the lifetime nor the refresh endpoint was verified here. A dead token usually means reauthenticating in the browser and restarting the proxy.
- **Storage keys.** `user`, `accessToken`, and `refreshToken` come from upstream documentation and match what this code expects, but they were not checked against a current live login.
- **The Android route.** Older upstream docs describe extracting tokens over `adb` from a rooted device's app-private SQLite database. That requires root, exposes far more than a token, and is deliberately **not recommended** here. Use the browser method.

## Usage accounting and other rough edges

`usage.prompt_tokens` and `usage.completion_tokens` are character counts, not tokenizer output. Conversation context is bounded by the 4000-character prompt cap, so long histories are silently trimmed. There is no rate limiting, no request logging, and no authentication layer between your client and the proxy. Inspect the code before relying on any of it, and don't route customer data, research material, or anything you wouldn't type into the upstream website through it.

## Repository notes

Files: `main.py`, `requirements.txt`, `.env.example`, `config.example.json`, `.gitignore`.

Provenance: this repository is a fork of `maakdan/fanzha-ai-proxy1`, taken at upstream revision `c10441f5`. That repository is itself a fork of `Octobersama/fanzha-ai-proxy`, which traces back to `lfzk550/fanzha-ai-proxy`; the upstream README states that the original source is `https://github.com/lfzk550/fanzha-ai-proxy` and that copyright outside derivative works stays with lfzk550 (Louis). The only change on this branch is this English README; `main.py` is byte-for-byte the upstream file. Commit names and email metadata alone do not establish authorship. No `LICENSE` file is present, and this README neither adds nor implies one; review [GitHub's licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) before assuming redistribution or commercial-use rights.
