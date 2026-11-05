---
name: inventory
description: Collect a read-only snapshot of Prometheus and Alertmanager (version, scale, peak scrape and rule durations, rules, targets), or interview the owner when there is no API access. First step of a Prometheus self-check. 盤點 Prometheus 與 Alertmanager 現況，取得尖峰 duration、rule、target。
argument-hint: "[prometheus-url] [alertmanager-url]"
---

# 盤點現況

## 有 API 可連時

執行唯讀蒐集腳本。它只送 HTTP GET，只用 Python 標準函式庫。

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/collect.py" \
  --prom http://localhost:9090 --am http://localhost:9093 \
  --out ./selfcheck-data --range 7d
```

- 需要驗證時，請使用者自行設定 `PROM_BEARER_TOKEN`、`AM_BEARER_TOKEN` 環境變數。**不要要求對方把 token 貼進對話。**
- `--range` 要涵蓋尖峰。問使用者尖峰何時發生；資料保留不足時縮短。
- 查詢太重失敗時，看 `selfcheck-data/errors.json`，縮短 `--range` 再跑。
- 提醒使用者：`selfcheck-data/` 可能含內部主機名和 label，不要 commit 或公開。把它加進 `.gitignore`。

跑完後讀 `buildinfo.json`、`runtimeinfo.json`、`flags.json`、`peaks.json`、`errors.json`。

## 不能連 API 時

請使用者在 UI 或內部工具查詢，一次給一組。

**第一組：規模與版本**
1. Prometheus 和 Alertmanager 版本（UI 的 Status → Runtime & Build Information）。
2. 部署方式與資源：K8s 或其他？CPU 與 memory 的 limit？是否為 HA？
3. `prometheus_tsdb_head_series` 和 `rate(prometheus_tsdb_head_samples_appended_total[5m])`。

**第二組：尖峰時間**（這組最重要）
```promql
# 每個 job 最大的 scrape 耗時
max by (job) (max_over_time(scrape_duration_seconds[7d]))
# 每個 rule group 最大的耗時
topk(10, max_over_time(prometheus_rule_group_last_duration_seconds[7d]))
# 被跳過的 evaluation
sum by (rule_group) (increase(prometheus_rule_group_iterations_missed_total[7d])) > 0
```
以及各 job 的 `scrape_interval`、各 group 的 `interval`。

**第三組：Rule**
請使用者提供 rule 檔，或 `/api/v1/rules` 的輸出。也可以只提供最重要的幾條。

## 要回報給使用者的摘要

| 項目 | 怎麼看 |
|---|---|
| 版本 | 是否還在支援期（見 `upgrade-path`） |
| 規模 | Series、每秒 sample、最大 target 的 sample 數 |
| Scrape | 每個 job 的 `scrape_interval` 與尖峰耗時的比值 |
| Rule | 每個 group 的 `interval` 與尖峰耗時的比值、跳過次數 |
| 異常跡象 | `for_state_resets`、`group_failures`、`checkpoint_failed`、`compactions_failed`、`wal_corruptions`、`restarts` |
| 資料來源 | 是否抓得到 Prometheus 自身 metric、Alertmanager、cAdvisor、kube-state-metrics |
| Go 設定 | `runtimeinfo` 的 `GOMAXPROCS`、`GOMEMLIMIT` 是否對齊 limit |

`scrape_duration_seconds` 不含 commit 時間，實際可見時間會更晚。

把比值超過 1 或異常計數大於 0 的項目標出來，並提醒：**尖峰數字只代表查詢範圍內抓得到的值**，`max_over_time` 看不到沒被抓到的那幾次。

## 單條 rule 或 target 的 API 欄位

內建 metric 最細只到 rule group，單條 rule 要看 API。

| API | 欄位 | 用途 |
|---|---|---|
| `/api/v1/rules` | `health`、`lastError`、`evaluationTime`、`lastEvaluation` | 單條 rule 是否正常、多慢 |
| `/api/v1/rules` | `state`、`duration`、`keepFiringFor`、`alerts[].activeAt` | Alert 狀態與 `for` |
| `/api/v1/rules`（group） | `interval`、`limit`、`evaluationTime`、`queryOffset`（2.53+） | Group 設定 |
| `/api/v1/targets` | `health`、`lastError`、`lastScrape`、`lastScrapeDuration`、`scrapeInterval`、`scrapeTimeout` | Target 狀態 |
| `/api/v1/targets/metadata` | `type`、`help`、`unit` | `rate` 要用在 counter 上 |
| `/api/v1/status/runtimeinfo` | `GOMAXPROCS`、`GOMEMLIMIT`、`corruptionCount`、`storageRetention`、`reloadConfigSuccess` | 行程狀態 |

完成後接著使用 `rule-contract`。
