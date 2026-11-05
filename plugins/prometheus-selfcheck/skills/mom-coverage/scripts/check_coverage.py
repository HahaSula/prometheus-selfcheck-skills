#!/usr/bin/env python3
"""Which monitor-of-monitor signals do the loaded rules cover? Standard library only.

  python3 check_coverage.py ./selfcheck-data [--extra-rules meta-prometheus-rules.json]

Matching is on metric names inside alert expressions. A match means an alert references the signal; it does not prove
the threshold is good or that the alert is routed correctly. Rules evaluated on another Prometheus are invisible here:
export that server's /api/v1/rules and pass it with --extra-rules.
"""
import argparse, json, os, re, sys
C = [
 ("L0", "外部 dead man's switch", r"vector\s*\(\s*1\s*\)|^\s*1\s*$", "critical", "Prometheus 或 Alertmanager 全掛時唯一能通知你的路徑；另外要確認它路由到外部服務"),
 ("L1", "資料新鮮度 heartbeat", r"time\(\)\s*-\s*(max|min)?\s*\(?\s*timestamp\(", "high", "vector(1) 不依賴資料；scrape 或 TSDB 壞掉時它依然正常"),
 ("L1", "關鍵 target 消失或停機", r"\bup\b|absent\(", "critical", "target 消失時依賴它的 alert 會安靜地不觸發"),
 ("L1", "Prometheus 自身停機（外部）", r'\bup\b.*prometheus|prometheus.*\bup\b', "critical", "要在另一台 Prometheus 上評估"),
 ("L2", "for 計時反覆重置", r"ALERTS_FOR_STATE", "high", "rule 沒報錯但永遠無法 firing 時，只有這個訊號會動"),
 ("L3", "Scrape 耗時接近 interval", r"scrape_duration_seconds", "high", "scrape 變慢會讓 rule 判斷錯誤卻不報錯"),
 ("L3", "實際 scrape 間隔拉長", r"prometheus_target_interval_length_seconds", "medium", "可以直接看出 tick 被丟掉"),
 ("L3", "Scrape 超過 sample／body 上限", r"prometheus_target_scrapes_exceeded_(sample_limit|body_size_limit)_total|prometheus_target_scrapes_sample_", "medium", "超過上限時整次 scrape 被丟棄"),
 ("L3", "Rule group 跳過 evaluation", r"prometheus_rule_group_iterations_missed_total", "high", "偵測延遲增加"),
 ("L3", "Rule group 耗時比值", r"prometheus_rule_group_last_duration_seconds", "high", "比值超過 1 就開始跳過，比失敗計數早"),
 ("L3", "Rule 評估失敗", r"prometheus_rule_evaluation_failures_total", "high", "查詢失敗或結果寫不進去"),
 ("L3", "Rule group 停止評估", r"prometheus_rule_group_last_evaluation_timestamp_seconds", "medium", "group 卡住時失敗數不會增加"),
 ("L3", "查詢排隊", r"prometheus_engine_queries|queue_time", "medium", "Rule 和 API 共用 slot，Grafana 重查詢會拖慢 alert"),
 ("L3", "通知送出失敗", r"prometheus_notifications_errors_total", "critical", "Prometheus 送不到 Alertmanager"),
 ("L3", "通知被丟棄", r"prometheus_notifications_dropped_total", "critical", "alert 已遺失"),
 ("L3", "Notifier queue 接近滿", r"prometheus_notifications_queue_length", "medium", "滿了才開始丟，這是前兆"),
 ("L3", "沒有可用的 Alertmanager", r"prometheus_notifications_alertmanagers_discovered", "critical", "服務發現失效時完全不會送出"),
 ("L3", "Alertmanager 通知失敗", r"alertmanager_notifications_failed_total", "critical", "Alertmanager 送不到接收端"),
 ("L3", "Alertmanager 設定載入失敗", r"alertmanager_config_last_reload_successful", "high", "新設定沒有生效"),
 ("L3", "Alertmanager 叢集異常", r"alertmanager_cluster_(members|failed_peers|health_score)", "medium", "可能造成重複或遺失通知；單台部署不適用"),
 ("L3", "設定檔載入失敗", r"prometheus_config_last_reload_successful", "high", "rule 或 scrape 的變更沒有生效"),
 ("L3", "重啟", r"process_start_time_seconds", "high", "crash loop 時 Watchdog 可能看不出來"),
 ("L3", "WAL 寫入失敗", r"prometheus_tsdb_wal_writes_failed_total", "critical", "資料和 rule 結果都寫不進去"),
 ("L3", "Checkpoint 失敗", r"prometheus_tsdb_checkpoint_creations_failed_total", "critical", "WAL 無法截斷；runtime 損毀在舊版只有這個訊號"),
 ("L3", "WAL 啟動修復", r"prometheus_tsdb_wal_corruptions_total", "high", "代表有資料遺失；要用 > 0 判斷，rate 算不出來"),
 ("L3", "WAL fsync 變慢", r"prometheus_tsdb_wal_fsync_duration_seconds", "medium", "慢磁碟先影響 compaction 與 retention"),
 ("L3", "Compaction 失敗", r"prometheus_tsdb_compactions_failed_total", "high", "可用空間不足時比 WAL 失敗更早出現"),
 ("L3", "Reload 停止或失敗", r"prometheus_tsdb_reloads_total|prometheus_tsdb_reloads_failures_total", "high", "Retention 在 reload 裡執行"),
 ("L3", "Head 未落成 block", r"prometheus_tsdb_head_max_time_seconds|prometheus_tsdb_head_min_time_seconds", "high", "新 block 停止產生時 time retention 會凍結"),
 ("L3", "Retention 落後", r"prometheus_tsdb_lowest_timestamp", "high", "直接判斷最舊資料是否超過 retention"),
 ("L3", "磁碟或 PVC 空間", r"kubelet_volume_stats_available_bytes|node_filesystem_avail_bytes", "critical", "內建 metric 看不到剩餘空間"),
 ("K8s", "CPU throttling", r"container_cpu_cfs_throttled_periods_total", "medium", "比 rule 變慢更早的訊號"),
 ("K8s", "記憶體接近 limit", r"container_memory_working_set_bytes|container_spec_memory_limit_bytes", "high", "接近 limit 會 OOM，也可能造成 WAL 損毀"),
 ("K8s", "OOMKilled／重啟原因", r"kube_pod_container_status_last_terminated_reason|kube_pod_container_status_restarts_total", "high", "Prometheus 自己看不到被殺的原因"),
]
def exprs(d):
    return [(r["name"], r.get("query", "")) for g in d.get("groups", []) for r in g.get("rules", []) if r.get("type") == "alerting"]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--extra-rules", action="append", default=[]); ap.add_argument("--json")
    a = ap.parse_args()
    try: data = json.load(open(os.path.join(a.dir, "rules.json")))
    except OSError: sys.exit("rules.json not found; run collect.py first")
    al = exprs(data)
    for f in a.extra_rules:
        x = json.load(open(f)); al += exprs(x.get("data", x))
    out = []
    for layer, name, rx, pri, why in C:
        hit = [n for n, q in al if re.search(rx, q)]
        out.append({"layer": layer, "signal": name, "covered": bool(hit), "alerts": hit[:5], "priority": pri, "why": why})
    miss = [o for o in out if not o["covered"]]; order = {"critical": 0, "high": 1, "medium": 2}
    print(f"# Monitor of monitor 覆蓋\n\n{len(al)} 條 alert，涵蓋 {len(out) - len(miss)}／{len(out)} 個訊號。\n\n## 缺少的訊號\n\n| 優先度 | 層 | 訊號 | 為什麼需要 |\n|---|---|---|---|")
    for o in sorted(miss, key=lambda x: order[x["priority"]]): print(f"| {o['priority']} | {o['layer']} | {o['signal']} | {o['why']} |")
    print("\n## 已涵蓋（請再確認門檻與路由）\n\n| 層 | 訊號 | Alert |\n|---|---|---|")
    for o in out:
        if o["covered"]: print(f"| {o['layer']} | {o['signal']} | {', '.join(o['alerts'])} |")
    p = None
    try: p = json.load(open(os.path.join(a.dir, "peaks.json")))["results"]
    except (OSError, KeyError, ValueError): pass
    if p:
        print("\n## 資料來源\n\n這些 metric 要先有人抓，規則才有資料可用。\n\n| 來源 | 狀態 |\n|---|---|")
        for k, label in [("has_self_metrics", "Prometheus 自身 metric"), ("has_am_metrics", "Alertmanager metric"), ("has_cadvisor", "cAdvisor"), ("has_ksm", "kube-state-metrics"), ("has_kubelet_volume", "kubelet volume stats"), ("has_node_exporter", "node_exporter"), ("has_blackbox", "blackbox 探測（這台）")]:
            print(f"| {label} | {'有' if p.get(k) else '查不到'} |")
    if a.json: json.dump(out, open(a.json, "w"), indent=1, ensure_ascii=False)
if __name__ == "__main__":
    main()
