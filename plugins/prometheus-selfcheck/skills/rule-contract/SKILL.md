---
name: rule-contract
description: Check whether each Prometheus alert rule's timing assumptions hold (range, offset, for, evaluation interval, scrape duration) and estimate worst-case detection latency. Use for alerts stuck in pending, alerts that never fire, or rule review. 檢查 alert rule 的時間契約、pending 抖動、偵測延遲上限，並給 rule 寫法建議。
argument-hint: "[snapshot-dir]"
---

# Rule 時間契約

## 符號

| 符號 | 意思 | 從哪裡看 |
|---|---|---|
| S | 設定的 `scrape_interval` | `/api/v1/targets` 的 `scrapeInterval` |
| d | 尖峰 scrape 耗時，不含 commit | `max_over_time(scrape_duration_seconds[7d])` |
| I | 實際 scrape 間隔 | d > S 時約 ⌈d/S⌉ × S，否則為 S |
| E | Group 的 evaluation interval | `prometheus_rule_group_interval_seconds` |
| T | Group 尖峰耗時 | `max_over_time(prometheus_rule_group_last_duration_seconds[7d])` |
| E' | 實際 evaluation 間隔 | T > E 時約 ⌈T/E⌉ × E |
| R | Range selector 長度 | Rule 表達式 |
| O | 查詢 offset | 表達式的 `offset`、或 `query_offset`（2.53+） |
| F | `for` | Rule 定義 |
| k | 要容忍的 scrape 失敗次數 | 建議至少 1 |

## 契約

| 契約 | 違反時 | 證據 |
|---|---|---|
| `d ≤ S` | Tick 被丟掉，實際間隔拉長 | 原始碼 |
| 沒有足夠 offset：`R ≥ (2 + k) × I + d` | 視窗內不足 2 個 sample，`rate` 回傳空，pending 被刪除，`for` 歸零 | 實測 |
| `O ≥ d`：`R ≥ (2 + k) × I` | 同上 | 實測 |
| 沒有 range 的 selector：`I + d < lookback`（預設 5m） | `up == 0` 這類規則間歇回傳空 | 上游 issue #19909 |
| `T ≤ E` | 跳過 evaluation，偵測變慢 | 實測 |
| `F` 遠大於 I | 太短時只涵蓋 1–2 個點 | 推算 |

**偵測延遲上限：** `I + max(O, d) + E' + T + F`。這是推算的上限，不是保證值。

**重要結論：**
- 公式只保證不回傳空結果。`rate` 仍可能被低估，門檻太貼近實際值時還是會抖動。
- `R = 2 × I` 不夠安全：只要 evaluation 剛好落在 scrape 進行中，當次就只剩 1 個點，d 很小也可能發生，取決於相位。
- 查詢失敗或 evaluation 被跳過時，pending **不會**被刪除；只有 evaluation 成功且結果裡沒有這個 alert，才會刪除。
- 事後查詢對不上 rule 當下的結果，因為事後看到的是已經寫入的資料。

## 執行

先確認已有 `inventory` 產生的資料，再執行：

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/analyze_rules.py" ./selfcheck-data --tolerate 1 --target-latency <秒>
```

2.53 以上有用 group 層級的 `query_offset` 時，要加 `--rule-files 'rules/*.yml'`，因為 Prometheus API 不回傳它。先問使用者有沒有用。

`--target-latency` 用使用者回答的通知時限。腳本用正規表示式解析表達式，遇到 recording rule、subquery 或對應不到 job 時會標記要人工確認。**不要把腳本結果當成最終結論，要讀它列出的表達式再判斷。**

沒有資料可分析時，請使用者提供最重要的幾條 rule 和對應的 S、d、E、T，手動套公式。

## 互動

1. 先報 FAIL 數量與最嚴重的 3 條，優先看使用者提到的重要 alert。
2. 用 `changes(ALERTS_FOR_STATE{alertname="X"}[1h])` 或蒐集到的 `for_state_resets` 驗證：計算結果 FAIL 的 alert，`for` 真的有在重置嗎？
3. 問：這條 alert 的尖峰是常態還是偶發？常態要修 scrape，偶發可以先用 offset 與 range 吸收。
4. 給出修改前後的表達式，並說明代價：offset 會讓通知變慢，range 拉長會平滑短暫尖峰，查詢也更貴。

## 修法

```yaml
# 2.53 以上：整個 group 設定
groups:
  - name: my-group
    query_offset: 3m
    rules:
      - alert: MyAlert
        expr: rate(my_metric_total{job="my-job"}[10m]) > 100
        for: 5m

# 2.53 以下：每個 selector 都要加 offset，實測有效
- alert: MyAlert
  expr: rate(my_metric_total{job="my-job"}[10m] offset 3m) > 100
  for: 5m
```

**Rule 的擺放：**
- 關鍵 alert 獨立成小 group，不要和昂貴的 recording rule 放一起。實測 CPU 嚴重不足時，小 group 延遲不變，重 group 變慢近一倍，還會漏掉短暫事件。
- Alert 依賴 recording rule 時放同一個 group，順序在後，否則可能讀到上一輪的值。
- 不同週期或互不相依的 rule 拆開。`concurrent-rule-eval` 在舊版有依賴判斷問題（3.1 修正），不要當第一選擇。

**`for` 與重啟：** `for` 小於 `--rules.alert.for-grace-period`（預設 10m）的 alert，重啟後會從零計時。

## 驗證修好了

```promql
# for 不再重置
changes(ALERTS_FOR_STATE{alertname="X"}[1h]) == 0
# rule 當下看得到足夠資料（需要 mom-coverage 的探針）
min_over_time(rule_probe:visible_samples:min[1h]) >= 2
```

接著使用 `mom-coverage`。
