---
name: alertmanager
description: Review Alertmanager configuration and health as part of the alert pipeline. Checks dead man's switch routing, repeat and group intervals, Prometheus to Alertmanager connectivity, HA, silences, and delivery failures. 檢查 Alertmanager 設定、dead man's switch 路由、HA 與通知失敗。
argument-hint: "[snapshot-dir]"
---

# Alertmanager 檢查

讀 `selfcheck-data/am_status.json`（`config.original`、`cluster`、`versionInfo`）、`am_alerts.json`、`am_silences.json`、`alertmanagers.json`。沒有資料時請使用者提供 `alertmanager.yml`，並先遮蔽 webhook URL 和 token。

**不要在報告裡重現 receiver 的 URL 或任何密鑰。**

## 設定檢查

| 檢查 | 怎麼看 | 為什麼重要 |
|---|---|---|
| Watchdog 有專屬路由 | `route.routes` 有比對它的項目，且指向外部接收端 | 沒有的話 heartbeat 可能進一般頻道，或根本沒送到外部 |
| Watchdog 的 `repeat_interval` 夠短 | 明顯小於外部服務的等待時間 | 否則外部服務會誤報 |
| Watchdog 不會被 inhibit 或 silence | 看 `inhibit_rules`、`am_silences` | 被壓掉就等於沒有 dead man's switch |
| 預設 receiver 真的有人看 | 頂層 `route.receiver` | 沒比對到的 alert 都去這裡 |
| `group_by` 合理 | `[...]` 或過少 label 的影響 | 一條 alert 可能被併進大 group，或產生通知風暴 |
| `group_wait`、`group_interval` | 加起來就是通知的額外延遲 | 要計入偵測延遲 |
| `send_resolved` | 接收端是否需要 | 影響值班判斷 |
| `inhibit_rules` 的 `equal` | 少了 `equal` 時範圍可能太大 | 可能壓掉不該壓的 alert |
| 長期 silence | `am_silences` 中 `endsAt` 很遠的項目 | 可能忘了移除 |

## 連線與 HA

```promql
prometheus_notifications_alertmanagers_discovered
rate(prometheus_notifications_errors_total[5m])
increase(prometheus_notifications_dropped_total[10m])
prometheus_notifications_queue_length / prometheus_notifications_queue_capacity
prometheus_notifications_latency_seconds{quantile="0.99"}

alertmanager_config_last_reload_successful
rate(alertmanager_notifications_failed_total[5m])
rate(alertmanager_notifications_total[5m])
alertmanager_cluster_members
alertmanager_cluster_failed_peers
alertmanager_cluster_health_score
```

- **HA：** Prometheus 要送給每一台 Alertmanager，不要透過 load balancer 只送一台。比對 `alertmanagers.json` 的數量和實際台數。
- `cluster.status` 應為 `ready`，`peers` 數量要等於台數。
- **2.x 的 notifier 只有一條 queue**，每批等所有 Alertmanager 回應，一台慢會拖累全部；3.x 新版每台各自一條。
- 2.x 的 `notifications_dropped_total` 大多只在所有 Alertmanager 都失敗或 queue 滿時才增加，要搭配 errors 看。

## 時間要算清楚

| 項目 | 公式 | 影響 |
|---|---|---|
| Alert 到期 | `EndsAt = 評估時間 + 4 × max(resend-delay, E)` | 續送中斷超過這個時間，Alertmanager 會自動 resolve，通知會 firing／resolved 交替 |
| Dead man's switch 的偵測延遲 | `4 × max(resend-delay, E)` 加上外部等待時間 | Prometheus 停機後，Alertmanager 還會繼續重送 Watchdog 到 `EndsAt`。實測停機 3 分鐘內外部仍持續收到 |
| 外部等待時間 | 要大於 `repeat_interval` 加上 `group_interval` | 否則會誤報 |

所以更快偵測 Prometheus 停機要靠外部 `up`，不能只靠 dead man's switch。

## 互動問題

1. 外部接收端是什麼（PagerDuty、Dead Man's Snitch、自建）？沒收到多久會通知？
2. 上一次實際測試 dead man's switch 是什麼時候？建議在維護時段做一次：暫停 Watchdog 路由，確認有收到通知。**這會影響線上，要由使用者決定並自己執行。**
3. Alertmanager 有幾台？跨節點或跨可用區嗎？

## 回報

列出通過與未通過的項目，未通過的附修改範例。Watchdog 路由範例：

```yaml
route:
  routes:
    - matchers: [alertname="Watchdog"]
      receiver: deadmans-switch
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 1m
receivers:
  - name: deadmans-switch
    webhook_configs:
      - url_file: /etc/alertmanager/secrets/deadman-url
        send_resolved: false
```

接著使用 `upgrade-path`。
