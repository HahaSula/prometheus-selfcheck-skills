# Policy 清單

嚴重度：FAIL 代表 alert 可能不會正確觸發或已有實質風險；WARN 代表餘裕不足；INFO 只供確認。

## Scrape

| Policy | 嚴重度 | 規則 | 為什麼 |
|---|---|---|---|
| `SCRAPE_NOT_MONITORED` | FAIL | 每個 scrape job 都要有 target 停機與消失的告警 | Target 不見時，依賴它的 alert 只會安靜地不觸發 |
| `SCRAPE_JOB_NO_TARGETS` | WARN | 設定的 scrape pool 要有 active target | 服務發現失效的訊號 |
| `SCRAPE_INTERVAL_TOO_SHORT` | FAIL／WARN | 尖峰 scrape 耗時不超過 interval，建議低於 80% | 超過時 tick 被丟掉，實際間隔拉長 |
| `SCRAPE_TIMEOUT_TOO_CLOSE` | WARN | 尖峰耗時低於 timeout 的 80% | 再慢一點就會 scrape 失敗 |
| `SCRAPE_TARGET_TOO_LARGE` | WARN | 單一 target 的 sample 數低於上限（可設定） | 每個 target 只有一條 goroutine |
| `SCRAPE_NO_SAMPLE_LIMIT` | INFO | 設定 `sample_limit` | 防止 target 暴增衝擊整台 |
| `SCRAPE_UNUSED_BY_ALERTS` | INFO | 確認沒有 alert 用到的 job 是否符合預期 | 可能漏了告警，或該降低保留 |

## Rule group

| Policy | 嚴重度 | 規則 | 為什麼 |
|---|---|---|---|
| `GROUP_INTERVAL_TOO_SHORT` | FAIL／WARN | 尖峰耗時不超過 interval，建議低於 80% | 超過就跳過 evaluation |
| `GROUP_EVAL_FAILING` | FAIL | 查詢範圍內沒有失敗 | 查詢或寫入有問題 |
| `RULE_UNHEALTHY` | FAIL | 每條 rule 的 health 為 ok | 看 `lastError` |

## Alert

| Policy | 嚴重度 | 規則 | 為什麼 |
|---|---|---|---|
| `RANGE_TOO_SHORT` | FAIL | 沒有足夠 offset 時 `R ≥ 2I + d`；`O ≥ d` 時 `R ≥ 2I` | 否則 `rate` 回傳空，`for` 歸零（實測） |
| `RANGE_LOW_MARGIN` | WARN | 再容忍 k 次 scrape 失敗：多加 `k × I` | 漏一次 scrape 就失效 |
| `RANGE_SHORTER_THAN_INTERVAL` | WARN | `R ≥ E` | 視窗之間有空隙 |
| `OFFSET_MISSING` | WARN | scrape 耗時明顯時，`O ≥ d` | 不然 rule 看不到最新資料 |
| `LOOKBACK_TOO_SHORT` | FAIL | 沒有 range 的 selector：`I + d < lookback` | 會間歇查不到 |
| `SERIES_NOT_FOUND` | WARN | Alert 用到的 series 應該存在 | 打錯名稱或 target 消失時永遠不觸發；但錯誤計數器這類 metric 平常不存在，要人工判斷 |
| `ALERT_FOR_RESETTING` | FAIL | `for` 計時不應反覆重置 | Rule 沒報錯卻無法 firing 的直接證據 |
| `FOR_ZERO` | WARN | 除非刻意，否則要有 `for` | 一次抖動就觸發或解除 |
| `FOR_TOO_SHORT` | WARN | `F ≥ 2I` | 否則只涵蓋 1–2 個點 |
| `FOR_NOT_RESTORED_ON_RESTART` | INFO | `F` 小於 grace period 時，重啟後從零計時 | 確認可以接受 |
| `CRITICAL_ALERT_IN_HEAVY_GROUP` | WARN | 關鍵 alert 不放在重 group | 實測資源緊張時會變慢或漏掉事件 |
| `RATE_ON_NON_COUNTER` | WARN | `rate` 要用在 counter | Gauge 的結果沒有意義 |
| `ALERT_MISSING_LABEL` | WARN | 必要 label（預設 `severity`） | 無法正確路由 |
| `ALERT_MISSING_ANNOTATION` | INFO | 必要 annotation（預設 `summary`） | 值班時缺乏說明 |

符號：S 設定的 scrape interval、d 尖峰 scrape 耗時、I 實際間隔（d > S 時約 ⌈d/S⌉ × S）、E evaluation interval、R range、O offset、F `for`、k 要容忍的失敗次數。
