# 實驗證據

環境：單機 Docker，受測 Prometheus 依情境 15 萬或 30 萬 series、15 秒 scrape 與 evaluation，由外部 meta Prometheus 和通知接收端量測。原始結果在 [`lab/results/`](lab/results/)，重現方式見 [`lab/README.md`](lab/README.md)。

**這些實驗驗證機制和順序。時間與門檻來自這個小規模環境，不能直接套用。**

## 1. Rule 可能不報錯地失效

2 分鐘間隔、100 秒延遲、`rate[5m]`、`for: 3m`，條件永遠成立，跑 25 分鐘：

| 版本 | 沒加 offset | 表達式 `offset 2m` | `query_offset: 2m` |
|---|---|---|---|
| 2.51.2 | 從未 firing，重置 9 次 | 540s 開始 firing | 不支援 |
| 2.55.1 | 從未 firing，重置 9 次 | 555s | 435s |
| 3.13.5 | 從未 firing，重置 10 次 | 510s | 375s |
| 3.15.1 | 從未 firing，重置 9 次 | 555s | 420s |

全程 `rule_evaluation_failures_total` 和 `iterations_missed_total` 都是 0。

## 2. Range 的公式

60 秒 interval、`for: 3m`，共 24 種組合，跑 24 分鐘（2.51.2、3.13.5）：

| Range | Offset | d = 5s | d = 20s | d = 40s | d = 55s |
|---|---|---|---|---|---|
| 2I | 0 | 2.51 失敗／3.13 正常 | 從未 firing | 從未 firing | 從未 firing |
| 2I | 1m | 正常 | 正常 | 正常 | 正常 |
| 3I、5I | 0 或 1m | 正常 | 正常 | 正常 | 正常 |

結論：沒有足夠 offset 時 `R ≥ 2I + d`；`O ≥ d` 時 `R ≥ 2I`。d = 5s 的差異應來自 scrape 與 evaluation 的相位，不能當作版本差異。矩陣只涵蓋 d < I。

## 3. Heartbeat 的盲點

通知接收端的最大間隔，正常約 35 秒：

| 故障 | Watchdog | 單一 target heartbeat | 帶 `for` 的 canary | 第一個抓到的訊號 |
|---|---|---|---|---|
| Canary 的 target 停機 | 35s | 240s | 213s | `up == 0`，70s |
| 其他 target 停機 | 35s | 35s | 35s | `up == 0`，80s |
| 查詢飽和 | 35s | 35s | 35s | 查詢 slot 比值，65s |
| CPU 降到 0.3 核 | 30s | 35s | 35s | throttling，80s |
| OOM crash loop（重啟 11 次） | 35s | 35s | 284s | `process_start_time_seconds`，10s |
| 磁碟滿 10 分鐘 | 35s | 558s | 583s | `wal_writes_failed`，15s |
| Alertmanager 停機 | 240s | 240s | 240s | `notifications_dropped`，15s |
| Prometheus 停機 3 分鐘 | 35s | 35s | 35s | 外部 `up == 0`，35s |

Prometheus 停機後，Alertmanager 會繼續 repeat 到 `EndsAt`，所以外部接收端仍持續收到。

## 4. CPU 邊際

事件翻轉到收到通知的延遲（`for: 30s`），每階段 1 次事件，只能看趨勢：

| CPU | Throttling | 重 group 耗時 ÷ interval | 大 target scrape | 獨立小 group | 重 group |
|---|---|---|---|---|---|
| 1.0 | 35% | 0.47 | 0.7s | 49.5s | 42s |
| 0.8 | 78% | 0.69 | 0.9s | 49.5s | 47s |
| 0.6 | 90% | 1.3 | 2.7s | 49.5s | 漏掉 |
| 0.5 | 99% | 1.6 | 3.1s | 49.5s | 90s |
| 0.4 | 99% | 2.8 | 7.0s | 49.5s | 84s |
| 0.25 | 100% | 3.0 | 5.9s | 49.5s | 漏掉 |
| 0.15 | 100% | 7.7 | 11.1s | 49.5s | 90s |

`auto-gomaxprocs`（1 核限制）：throttling 37% → 3%，大 target scrape 2.8s → 0.7s。

## 5. 記憶體邊際

| Limit ÷ working set | 執行中重啟 | 非正常重啟後再重啟 | 帶 `for` canary 最大中斷 |
|---|---|---|---|
| 1.5 | 0 | 0 | 30s |
| 1.3 | 0 | 0 | 35s |
| 1.2 | 0 | 0 | 30s |
| 1.1 | 1 | 2 | 35s |
| 1.0 | 4 | 6 | 240s |

Replay 只有 8–13 秒，所以這裡無法驗證大型環境 replay 時的記憶體尖峰。

## 6. 儲存

**IOPS：** 探測確認限速生效（寫 16MB 從 0 秒變 98 秒）。10 IOPS 時 WAL fsync p99 達 4.8–7.7 秒，reload 頻率約減半，scrape 和 rule 幾乎不受影響。記憶體充足，page cache 吃緊時可能不同。

**真實磁碟完全寫滿（2.51.2、2.55.1、3.13.5、3.15.1）：**

| 階段 | 結果 |
|---|---|
| 寫滿 10 分鐘 | WAL 與 rule 寫入失敗；reload 正常；compaction 失敗數為 0；block 數不變 |
| 釋放空間 | 寫入立刻恢復；下一次 compaction 的 checkpoint 失敗；WAL 83MB → 約 135MB；`wal_corruptions_total` 仍是 0 |
| 重啟 | 修復 WAL，`wal_corruptions_total` 變 1 |

2.51.2、3.13.5 印 `unexpected full record`；2.55.1、3.15.1 印 `unexpected first record`。OOM 則是 `unexpected checksum`。

**空間被 compaction 用完（2.51.2／3.13.5）：**

| 時間 | 訊號 |
|---|---|
| 145s／150s | `compactions_failed_total` 增加 |
| 360s／370s | `wal_writes_failed_total` 增加 |
| 565s／575s | `reloads_total` 5 分鐘為 0 |
| 765s／770s | 最舊資料超過 retention |

## 7. 升級

**回退：** 目前所有版本寫 index 都是 `FormatV2`。切換前後的 sample 筆數相同：

| 切換 | 前 | 後 |
|---|---|---|
| 3.13.5 → 2.51.2 | 21000 | 21000 |
| 3.13.5 → 2.55.1 | 20500 | 20500 |
| 2.51.2 → 3.13.5 | 20500 | 20500 |

500 條 gauge、正常關機。官方文件仍只支援回退到 2.55。

**Alert 恢復 3 萬個實例（debug log 時間）：** 2.51.2 1.5s、2.55.1 0.9s、3.13.5 0.7s。

**相同負載的資源比較：** 30 萬 series、每秒約 2 萬 sample、15 分鐘 block，三個版本同時跑，量測 40 分鐘，各一次。

| 指標（平均） | 2.51.2 | 2.55.1 | 3.13.5 |
|---|---|---|---|
| CPU（核） | 0.66 | 0.70 | 0.65 |
| RSS | 1.40GB | 1.25GB | 1.26GB |
| 記憶體配置速率 | 67MB/s | 52MB/s | 53MB/s |
| 大 target scrape | 0.68s | 0.71s | 0.78s |
| 重 rule group | 6.5s | 6.9s | 6.8s |
| WAL 大小 | 316MB | 317MB | 316MB |
| 非正常重啟的 replay | 6.1s | 4.7s | 6.0s |

CPU、scrape、rule 耗時沒有明顯差異，2.55 以上記憶體約少 10%。同時期機器上有其他實驗，幾個百分點內的差距不能當成版本差異。

## 上游參考

- [#13027](https://github.com/prometheus/prometheus/issues/13027)、[#13152](https://github.com/prometheus/prometheus/issues/13152) WAL 損毀後無法截斷
- [#19909](https://github.com/prometheus/prometheus/issues/19909) Lookback 邊界
- [PR #20161](https://github.com/prometheus/prometheus/pull/20161) Reload ticker
- [#11088](https://github.com/prometheus/prometheus/discussions/11088)、[#11807](https://github.com/prometheus/prometheus/issues/11807) Rule 與延遲資料
- [Grafana Labs：metamonitoring 架構](https://grafana.com/blog/2021/04/08/how-we-use-metamonitoring-prometheus-servers-to-monitor-all-other-prometheus-servers-at-grafana-labs/)
- [PromLabs：End-to-End Watchdog Alerts](https://training.promlabs.com/training/monitoring-and-debugging-prometheus/metrics-based-meta-monitoring/end-to-end-watchdog-alerts/)
