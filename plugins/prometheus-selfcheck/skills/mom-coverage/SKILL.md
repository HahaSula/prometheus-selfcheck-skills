---
name: mom-coverage
description: Audit monitor-of-monitor coverage for Prometheus. Find missing meta-monitoring signals such as dead man's switch, data heartbeat, known-true canary, rule group lag, WAL and notifier failures, then propose alert rules. 盤點 monitor of monitor 缺口，並產生 canary 與告警規則。
argument-hint: "[snapshot-dir]"
---

# Monitor of monitor 覆蓋

## 四層模型

| 層 | 做法 | 驗證什麼 | 放在哪裡 |
|---|---|---|---|
| **L0 外部鏈路** | `Watchdog`（`vector(1)`）送到外部 dead man's switch | Prometheus → Alertmanager → 通知管道活著 | 外部服務，不依賴同一個叢集 |
| **L1 資料新鮮度** | 每個關鍵 job 一條 heartbeat | Scrape → TSDB → 查詢有新資料 | 受測 Prometheus，通知送外部 |
| **L2 判斷正確性** | 已知成立、帶 `for` 的 canary | `for` 不會重置，通知能在時限內到達 | 和真實 rule 相同負載的 group |
| **L3 邊際** | Scrape、rule、查詢、通知、WAL、資源的告警 | 距離失效還有多遠 | 外部 meta Prometheus |

**只有 `Watchdog` 不夠。** 實測中它在這些故障都幾乎不受影響：target 停機、查詢飽和、CPU 受限、OOM crash loop、磁碟完全寫滿。

| 故障 | Watchdog | 資料 heartbeat | 帶 `for` 的 canary |
|---|---|---|---|
| Canary 的 target 停機 | 沒發現 | 發現 | 發現 |
| 其他 target 停機 | 沒發現 | 沒發現（每個 job 要各自一條） | 沒發現 |
| 慢 scrape，alert 無法 firing | 沒發現 | 沒發現 | 沒發現，要看 `for` 重置 |
| 查詢飽和、CPU 受限 | 沒發現 | 沒發現 | 沒發現，要看 group 耗時比值 |
| OOM crash loop | 沒發現 | 沒發現 | **發現** |
| 磁碟完全寫滿 | 沒發現 | 發現 | 發現 |
| Alertmanager 停機 | 發現 | 發現 | 發現 |
| Prometheus 停機 | **約 4 分鐘後才發現** | 同左 | 同左 |

## 如果已經用 blackbox 探測

另一台 Prometheus 加 blackbox 是好的底層，但它只證明行程活著。實測 8 種故障裡，`/-/healthy`、`/-/ready` 只明確抓到 Prometheus 和 Alertmanager 自己停機：

| 故障 | 存活探測 | 真正抓到的訊號 |
|---|---|---|
| CPU 不足 | 全部成功 | `iterations_missed` |
| 查詢飽和 | 實際查詢才會失敗（10／14） | 查詢 slot 比值 |
| Target 停機 | 全部成功 | `up == 0` |
| 接收端停機，通知全斷 | 全部成功 | `alertmanager_notifications_failed_total` |
| OOM crash loop | 18／19 成功，幾乎看不出來 | `process_start_time_seconds` |
| 磁碟完全寫滿 | 全部成功 | `wal_writes_failed_total` |

建議依序往上補：
1. 保留 blackbox，加一個真的跑查詢的探測，並用 `fail_if_body_not_matches_regexp` 驗證回應。
2. 外部 Prometheus 直接抓兩者的 `/metrics`。
3. 用 `/federate` 拉 `up`、`scrape_duration_seconds`、`ALERTS*`。
4. 受測 Prometheus 加帶 `for` 的資料 canary。
5. Watchdog 送到真正外部的 dead man's switch，定期實測。
6. K8s 環境補 cAdvisor、kube-state-metrics、PVC 用量。

**一定要問：外部 Prometheus 的告警走哪條路？** 如果也走被監控的同一組 Alertmanager，Alertmanager 掛掉時它無法通知。它本身也需要 dead man's switch。

## 執行

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/check_coverage.py" ./selfcheck-data
```

腳本只比對 alert 表達式裡的 metric 名稱，所以**命中只代表有規則提到這個訊號**，不代表門檻合理或有正確路由。規則放在另一台 Prometheus 時，先匯出該台的 `/api/v1/rules`，再用 `--extra-rules <檔案>` 一起分析。

## 互動

腳本看不到架構，所以要問：

1. **現在怎麼監控這台 Prometheus 和 Alertmanager？** 例如 blackbox、另一台 Prometheus 抓 metric、Dead Man's Snitch。外部那台自己的告警走哪條路？
2. **有外部 dead man's switch 嗎？** Watchdog 送到哪裡？沒收到多久會通知誰？上次實際測試是什麼時候？
3. **有另一台 Prometheus 監控這台嗎？** 它和受測對象會不會一起掛（同節點、同儲存、同叢集）？
4. **關鍵 job 有哪些？** 用來產生 L1 heartbeat。

## 產生規則

依缺口從這些範本挑選，再依環境調整，不要整份照抄：

- [references/canary.yml](references/canary.yml)：L0 到 L2。
- [references/meta-alerts.yml](references/meta-alerts.yml)：L3，已通過 `promtool check rules`，門檻以 30s scrape 與 evaluation 計算。
- [references/rule-probes.yml](references/rule-probes.yml)：記錄 rule 當下看到的資料。

**調整重點：**
- 把 `interval="30s"`、`> 25`、`> 40` 換成對方的 interval。
- `job="prometheus"` 和 `container="prometheus"` 換成實際 label。
- `HeadNotCompacting` 的 4 小時以預設 2h block 為準。
- `AlertForTimerResetting` 會在重啟時誤報，要對照 `PrometheusRestarted`。
- `BlockReloadStalled` 啟動初期可能誤報，保留 `for`。

**每條規則都有 `scope` label，對應監控對象：**

| `scope` | 對象 | 建議負責人 | 放在哪裡 |
|---|---|---|---|
| `target` | 單一 target | 服務負責人 | 受測 Prometheus，或 `/federate` 到 meta |
| `scrape_interval` | 同 interval 的所有 target | 平台 | Meta |
| `rule_group` | 一個 rule group | Rule 作者 | Meta |
| `alert` | 單一 alert 實例 | Rule 作者 | 受測 Prometheus，或 `/federate` 到 meta |
| `prometheus` | 整個行程 | 平台 | Meta |
| `alertmanager` | 單台 Alertmanager | 平台 | Meta |
| `container` | Prometheus 容器 | 平台 | Meta |

沒有內建 metric 能精確到單條 rule，要用 `/api/v1/rules` 或探針。`up`、`scrape_duration_seconds`、`ALERTS*` 不在 `/metrics` 裡，meta 要用 `/federate` 才拿得到：

```yaml
- job_name: federate
  honor_labels: true
  metrics_path: /federate
  params:
    'match[]':
      - '{__name__=~"up|scrape_duration_seconds|scrape_samples_scraped|ALERTS|ALERTS_FOR_STATE"}'
  static_configs: [{ targets: ['prometheus:9090'] }]
```

寫完後請對方執行驗證：

```bash
promtool check rules <file>
```

## 判斷訊號的順序

| 壓力 | 依序出現 | 全程不會動 |
|---|---|---|
| CPU 不足 | throttling → group 耗時比值、跳過 evaluation → group 停止 → 最後才是 scrape 接近 timeout | `up`、rule 失敗數、heartbeat |
| 查詢飽和 | 查詢 slot 比值、跳過 evaluation → group 耗時 → queue time | `up`、scrape、rule 失敗數 |
| 慢磁碟 | WAL fsync → retention 落後 → Head 未落成 block | `up`、scrape、rule |
| 可用空間被 compaction 用完 | compaction 失敗 → WAL 寫入失敗 → canary 中斷 → reload 停止 | Watchdog |
| 磁碟突然寫滿 | WAL 寫入失敗、rule 失敗 → canary 中斷 | Watchdog、reload、compaction 失敗數 |

實驗環境規模有限，順序可以參考，時間不能直接套用。

接著使用 `capacity-margin`。
