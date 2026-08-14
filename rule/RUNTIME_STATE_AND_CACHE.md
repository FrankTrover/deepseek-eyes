# RUNTIME_STATE_AND_CACHE — Source/Region/Cache 工程规格

## 1. SourceRegistry ownership

唯一 owner：
```text
Eyes Core Runtime
```

Adapter 只能调用：
```python
await registry.register(media, origin=...)
```

不能自己生成 ref。

---

## 2. SourceRef

格式：
```text
src_<url-safe random>
```

熵：
```text
>=128 bits
```

属性：
- opaque；
- 不含 path/filename/user id；
- process-local；
- TTL；
- restart invalid。

Source record：
```text
ref
raw_digest
canonical_digest
mime_type
width
height
canonical_bytes
origin
created_at
last_access_at
hard_expires_at
pin_count
```

---

## 3. RegionRef

绑定：
```text
source_ref
source canonical_digest
normalized bbox
```

source digest 变化/不存在：
```text
REGION_STALE
```

Region 不保存重复图像 bytes。

---

## 4. 生命周期

默认：
```text
idle TTL = 20 min
hard TTL = 60 min
```

`resolve()` 更新 last_access。

hard TTL 不因访问无限延长。

正在 provider call：
```text
pin_count += 1
```

结束 finally：
```text
pin_count -= 1
```

Pinned source 不 evict。

---

## 5. 容量

```text
max live source refs = 32
max canonical source memory = 256 MiB
```

超限：
1. delete expired；
2. evict oldest unpinned LRU；
3. still cannot fit -> `SOURCE_REGISTRY_FULL`。

不得把用户图偷偷写磁盘以“解决内存不足”。

---

## 6. Concurrency

Metadata：
```text
asyncio.Lock
```

Canonical media：
```text
immutable bytes
```

不要在 lock 内：
- decode 大图；
- network call；
- MiMo call。

流程：
1. expensive canonicalization outside registry lock；
2. acquire lock；
3. capacity check；
4. insert immutable record；
5. release。

---

## 7. ExactObservationCache

默认仅内存。

```text
max entries = 128
max serialized payload = 64 MiB
TTL = 60 min
```

Key：
```text
contract_version
vision_schema_version
model
prompt_version
mode
detail/planner decision
observation_question canonical bytes
ordered source canonical digests
ordered crop bboxes
thinking mode
```

不能只用 source_ref，因为相同图可以有不同 Runtime ref。

---

## 8. Cache 数据敏感性

视觉结果可能含：
- OCR；
- path；
- source code；
- error text。

所以：
```text
disk exact result cache = OFF
```

默认进程退出清空。

“cache 存在”与“不持久化视觉历史”不冲突，因为 MVP cache 是短期内存状态，不是永久存储。

---

## 9. PreprocessCache

只在内存。

Key：
```text
canonical_digest + operation + params
```

如：
```text
crop bbox
overview resize
```

Max：
```text
128 MiB
```

与 SourceRegistry 总内存预算分别统计，并在 Control Center/doctor 中可见。

---

## 10. SingleFlight

Key = ExactObservationCache key。

结构：
```text
dict[key, asyncio.Future]
```

请求：
- first creates Future and provider work；
- followers await Future；
- completion/failure always removes key in finally。

Failure 不进入 success cache。

---

## 11. Cleanup

Background timer 只做本地内存 housekeeping：
```text
every 60s
```

它不是“后台截图/后台 AI 工作”。

Process shutdown：
- cancel cleanup；
- clear registries；
- no media disk residue。
