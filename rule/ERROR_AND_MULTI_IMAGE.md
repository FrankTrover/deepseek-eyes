# ERROR_AND_MULTI_IMAGE — 统一错误与多图规则

## 1. Error taxonomy

### Contract
```text
CONTRACT_VERSION_MISMATCH
REQUEST_INVALID
OUTPUT_SCHEMA_INVALID
OUTPUT_PROTOCOL_VIOLATION
OUTPUT_TRUNCATED
```

### Source
```text
SOURCE_NOT_FOUND
SOURCE_EXPIRED
SOURCE_FORBIDDEN
SOURCE_TOO_LARGE
SOURCE_DECODE_FAILED
SOURCE_REGISTRY_FULL
REGION_INVALID
REGION_STALE
```

### Permission
```text
CAPTURE_NOT_ALLOWED
CAPTURE_CANCELLED
CAPTURE_CONFIRMATION_DENIED
FULLSCREEN_NOT_ENABLED
```

### Credential/Provider
```text
TOKEN_PLAN_CONFIG_INVALID
CREDENTIAL_MISSING
PROVIDER_AUTH_FAILED
PROVIDER_BALANCE_OR_PLAN_EXHAUSTED
PROVIDER_FORBIDDEN
PROVIDER_BAD_REQUEST
PROVIDER_CONTENT_FILTERED
PROVIDER_RATE_LIMIT
PROVIDER_SERVER_ERROR
PROVIDER_OVERLOADED
PROVIDER_CONNECT_TIMEOUT
PROVIDER_TIMEOUT_AMBIGUOUS
```

### Budget
```text
LOCAL_RATE_LIMIT
CONCURRENCY_LIMIT
OBSERVATION_BUDGET_EXCEEDED
```

---

## 2. Retry matrix

No retry:
- 400；
- 401；
- 402；
- 403；
- 404；
- content filter；
- invalid source；
- invalid contract；
- permission deny。

Bounded retry:
- 429；
- 500；
- 503；
- connect failure before request transmission。

Ambiguous read timeout after request may have been processed:
```text
do not auto retry
```
Return:
```text
possible_duplicate_billing=true
```

---

## 3. source_ref missing/expired

Never:
```text
scan disk to find the image
```

Return explicit error so Host can:
- re-register original attachment if it still owns it；
- or ask user to attach again。

---

## 4. Unsupported image format

Runtime should normalize:
- PNG；
- JPEG；
- WebP；
- BMP/static GIF where explicitly allowed。

Animated GIF default:
```text
reject
```

Do not send unknown binary just because extension ends `.png`.

---

## 5. Multi-image

Contract:
```text
1..8 sources
```

Mode constraints:

### compare
```text
2..8
```

### non-compare
normally:
```text
1..4
```
Runtime can reject obviously excessive source count under selected budget profile.

Each evidence:
```text
must bind source_ref
```

Cross-image statement:
```text
inference
```
unless it is a direct comparison of exact visible values explicitly represented by evidence from both sources.

---

## 6. Multi-image token guard

Before provider:
```text
sum estimated image tokens
sum canonical encoded bytes
```

Budget profiles set total image-token soft limits.

If over:
- use requested focus/crop；
- or split compare into deterministic/targeted phases；
- never silently omit a source and claim complete comparison。

---

## 7. Error to Host

MCP tool errors should be structured and also use SDK `is_error` semantics where appropriate.

Host must check error before trusting structured content.

Never return:
```text
status=ok
```
with hidden failed provider state.
