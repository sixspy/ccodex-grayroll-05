# grayroll-05 说明：发布说明须含变更单号（独验收）

领域：应用开发 · 全栈开发。登录导航与本地单文件库（`frontend/vue.global.prod.js`）路径沿用，未改登录/导航骨架。

## 独验收口径

1. 用**无变更单号发布说明夹具**复现后：缺单号 → 正式 releases 与 flag_rules **零增**；
2. 合规发布（说明内含可识别变更单号）后：历史页/历史口可**按单号检索**，单号在 releases 与 change_history **双写跨表**；
3. 两路会话并发抢**同一开关发布锁**：恰一路成，败方 409 且**正式账零改**，只留争用痕。

夹具：`fixtures/grayroll_05_note_missing_ticket.json`（负例）、`fixtures/grayroll_05_note_with_ticket.json`（正例）。
单号正则单一事实源：`backend/tickets.py`（`CHG-20261126-05`、`变更单号：2026112605`、`OPS_123`、`#12345` 等均可识别；“无变更单号……”“灰度30%”不误判）。前端 `frontend/js/ticket.js` 为同口径 UI 镜像，以后端校验为准。

---

## 命运取舍（当班拍板并封死）

**本单择甲路径，且只走甲路径，禁止与乙路径混用，禁止第三路折中。**

- 甲路径定义即题面口径：**缺单号则发布与规则正式零增、整笔拒写**。
- 落地语义：`POST /api/releases/publish` 的 `summary` 抽不到变更单号时，在拿锁与任何写库之前直接 `400`；`releases`、`flag_rules` 行数相对请求前零增，**连 `release_shadows` 影子行也不写**——不开放“缺单号只落影子草案”的乙式侧门。
- 规则正式写口 `rules_svc.replace_official_rule()` 内置同一单号闸；规则影子/发布影子升正必须带单号出处，否则 `400`，正式规则零改。
- 乙路径（缺单号只许影子草案、正式零改）**本单明确不取**：因此不会把缺单号请求改写成影子，也不会“前端放行、后端补默认单号”等任何折中。
- 应急 `restore`/`kill` 等非 `notify.batch_push` 发布说明写口不借本单混入单号口径，保持原应急语义；本单封的是“发布说明（含其规则正式账与影子升正）”这一条正确性口径。

一致性由测试封死：`tests/test_grayroll_05_jia.py` 同时断言缺单号正式零增**与**影子侧门也零增。若实现改成乙路径或任何折中，该文件立即红，记为偏航并拒写。

---

## 红测链（先见红，后改服务）

三红先写成测试源文件并分别跑测试器见红，随后才动五个服务与发布页/历史页：

| 文件 | 盯的假绿面 | 修复所属服务 | 见红时实况 |
| --- | --- | --- | --- |
| `tests/test_grayroll_05_jia.py` | 写口正式账实：缺单号仍 200 建正式 release + rule；甲路径还要求影子侧门封死 | 发布服务 `releases_svc.py`、规则服务 `rules_svc.py`、单号核 `tickets.py` | `实际 200: {"mode":"official"...}`；影子用例 `200 {"mode":"shadow"...}` |
| `tests/test_grayroll_05_yi.py` | 另一面假绿：历史口无单号字段/检索，未升正影子被全量返回冒充“已发布齐套”；求值页单号不同源 | 历史服务 `history_svc.py`、审计/历史写口 `audit_svc.py`（双写）、求值服务 `evaluate_svc.py` | `None != 'CHG-20261126-11'`；`scope=official` 仍返回种子全量行 |
| `tests/test_grayroll_05_zheng.py` | 锁：两路会话抢同开关发布锁无锁双成 | 锁 `locks.py` + 审计争用痕 `audit_svc.py` | 不同账号与同账号两路均 `[200, 200]`、marks=0 |

首跑结果：三文件共 **6 failed, 1 passed**（唯一 passed 是合规正例对照，本就不该红），三个文件各自至少一红，满足“三红分文件分别见红”。

### 分服务救绿（不是单点校验交差）

- 甲红：单号闸在发布服务写口最前面；规则服务抽出 `replace_official_rule()` 正式写口并自带单号校验，发布/升正都走它，保证 release 与 rule 两本正式账同生共死、缺单整笔回滚。
- 乙红：`change_history` 增 `change_ticket`、`stage`（official/shadow）列；`audit_svc.write_history()` 双写单号与正影标记；历史服务支持 `?ticket=&scope=official|shadow` 并回传 `official` 布尔；求值服务回传同一 `change_ticket`。`releases`/`release_shadows`/`rule_shadows` 同步加 `change_ticket` 列，旧库由 `db.migrate()` 幂等 ALTER + 正则回填。
- 争用红：见下节，锁机制独立救绿。

三红分属不同服务面，没有用“发布说明必须含变更单号”一个单点校验去冒充另外两红的修复。

---

## 争用核销（独立段）

脚本：`scripts/release_ticket_regex_05.py`，另提供无扩展名包装 `scripts/release_ticket_regex_05`。
它用两个独立会话（`admin` / `writer`，并有用例覆盖同账号两路不同 token）登录，经 `threading.Barrier` 同时向同一写口
`POST /api/releases/publish`（固定 `flag-release:5:1` 锁键）打出双请求，核销状态码、正式账增量与争用痕。

运行：

```bash
python scripts/release_ticket_regex_05.py                 # 自起临时 uvicorn + GRAYROLL_DB 临时库
python scripts/release_ticket_regex_05.py --base-url http://127.0.0.1:8020
./scripts/release_ticket_regex_05                         # 无扩展名 mandated 入口
```

实测判决：

```text
[pair] lock_key=flag-release:5:1 statuses=[200, 409]
[accounts] official releases 0 -> 1 (delta=1); contention marks +1
ZHENG-VERDICT: GREEN 一成一争用拒绝，败方零正式改，争用痕已核销
```

锁修复要点（`backend/locks.py`）：旧实现“先 SELECT 后 `ON CONFLICT DO UPDATE`”会让败者在提交点后偷锁双成；仅改 `BEGIN IMMEDIATE` 仍不够（败者会在 SQLite 写锁队列阻塞到胜者 finally 删行之后读到空行）。现采用：

1. 进程内按 key 的**非阻塞互斥锁**：抢不到立即判争用，不排队等下一任（本部署单 uvicorn 进程 + 线程池，两路会话必同进程）；
2. `row_locks` 主键**直插**持久占锁，冲突且锁未过期/owner 不同才记争用并 409，绝不无条件 upsert 偷锁；
3. `owner_nonce` 每路请求唯一，同账号两路会话也不互认重入；
4. 争用同事务写 `contention_marks` 与 `audit_log(lock_contention)`，可经 `GET /api/locks/contention` 与审计口同源读到。

稳定性：`tests/test_grayroll_05_zheng.py` 连续 **15 轮**全部 2 passed；脚本自建服务与 `--base-url` 两种模式均退出码 0。

---

## 五页同源逐页点开

三绿后实际起 `uvicorn app:app --app-dir backend --port 8020`，并用 jsdom 加载本地 `frontend/vue.global.prod.js` 真实挂载五个页面、打活后端断言模板编译与数据行：

```text
OPEN-OK 覆盖页 rows=2   （单点覆盖，u-9999 / checkout.new_pay 实数据）
OPEN-OK 求值页 rows=5   （正式规则板；求值结果含 version + change_ticket）
OPEN-OK 历史页 rows=7   （单号检索框 + 正式/影子镜头；渲染 CHG-20261126-05）
OPEN-OK 开关页 rows=5   （notify.batch_push / checkout.new_pay）
OPEN-OK 发布页 rows=4    （正式/影子两表；渲染 notify.batch_push 与单号）
FIVE-PAGES-VERDICT: GREEN
```

- 覆盖页 `/api/overrides`、求值页 `/api/evaluate` + `/api/evaluate/board`、历史页 `/api/history`、开关页 `/api/flags`、发布页 `/api/releases` 全部 200；
- `/`、`/assets/js/app.js`、`api.js`、新增 `ticket.js`、五个视图、本地 Vue 库、CSS 全部 200；
- 历史页交互：`?ticket=CHG-20261126-05` 命中正式 publish；未升正的 `CHG-20261126-06` 在 `scope=official` 为 `[]`、在 `scope=shadow` 以 `stage=shadow/official=false` 出现，升正后才进入正式镜头——乙红的齐套假绿被封死。

## 测试总闸

```bash
python -m pytest
# 17 passed（原 10 测 + grayroll-05 七测），连跑两遍全绿
```

## 变更文件

- 测试与夹具：`tests/test_grayroll_05_jia.py`、`tests/test_grayroll_05_yi.py`、`tests/test_grayroll_05_zheng.py`、`tests/conftest.py`、`fixtures/grayroll_05_note_*.json`
- 并发脚本：`scripts/release_ticket_regex_05.py`、`scripts/release_ticket_regex_05`
- 后端：`tickets.py`（新）、`releases_svc.py`、`rules_svc.py`、`history_svc.py`、`evaluate_svc.py`、`audit_svc.py`、`locks.py`、`db.py`
- 前端：`js/ticket.js`（新）、`js/views/releases_view.js`、`js/views/history_view.js`、`js/views/evaluate_view.js`、`js/views/rules_view.js`、`css/app.css`
- 既有测试适配新口径：`tests/test_smoke.py`、`tests/test_paths.py` 的旧说明补合规单号；`tests/test_restore_compare.py` 增加 flag4 killed 复位，消除共享单文件库污染导致的既存偶发失败。
