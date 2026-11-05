#!/usr/bin/env python3
"""Read-only snapshot of a Prometheus and optional Alertmanager for the self-check skills.

Only sends HTTP GET requests. Standard library only.
  python3 collect.py --prom http://localhost:9090 [--am http://localhost:9093] --out ./selfcheck-data [--range 7d]
Authentication: set PROM_BEARER_TOKEN / AM_BEARER_TOKEN in the environment. Tokens are never written to disk.
The output can contain internal hostnames and label values. Keep it private.
"""
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request

def get(base, path, token=None, timeout=60):
    req = urllib.request.Request(base.rstrip("/") + path, method="GET")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom", required=True); ap.add_argument("--am"); ap.add_argument("--out", default="selfcheck-data")
    ap.add_argument("--range", default="7d", help="look-back window for peak queries")
    a = ap.parse_args()
    if not re.fullmatch(r"\d+[smhdw]", a.range):
        sys.exit("--range must look like 7d")
    os.makedirs(a.out, exist_ok=True)
    tok, atok = os.environ.get("PROM_BEARER_TOKEN"), os.environ.get("AM_BEARER_TOKEN")
    errors, W = {}, a.range

    def save(name, obj):
        with open(os.path.join(a.out, name + ".json"), "w") as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)

    def fetch(name, path):
        try:
            d = get(a.prom, path, tok)
            save(name, d.get("data", d)); return d.get("data", d)
        except Exception as e:
            errors[name] = str(e)[:300]; return None

    for name, path in {"buildinfo": "/api/v1/status/buildinfo", "flags": "/api/v1/status/flags", "runtimeinfo": "/api/v1/status/runtimeinfo",
                       "tsdb": "/api/v1/status/tsdb", "walreplay": "/api/v1/status/walreplay", "alertmanagers": "/api/v1/alertmanagers",
                       "rules": "/api/v1/rules", "targets": "/api/v1/targets?state=active", "config": "/api/v1/status/config", "metadata": "/api/v1/metadata"}.items():
        fetch(name, path)

    def q(expr):
        try:
            return get(a.prom, "/api/v1/query?query=" + urllib.parse.quote(expr), tok, 120)["data"]["result"]
        except Exception as e:
            errors["query: " + expr[:70]] = str(e)[:200]; return None

    peaks = {
        "scrape_duration_peak": f"max by (job) (max_over_time(scrape_duration_seconds[{W}]))",
        "scrape_duration_now": "max by (job) (scrape_duration_seconds)",
        "scrape_samples_peak": f"max by (job) (max_over_time(scrape_samples_scraped[{W}]))",
        "up_min": f"min by (job) (min_over_time(up[{W}]))",
        "group_duration_peak": f"max by (rule_group) (max_over_time(prometheus_rule_group_last_duration_seconds[{W}]))",
        "group_duration_now": "max by (rule_group) (prometheus_rule_group_last_duration_seconds)",
        "group_missed": f"sum by (rule_group) (increase(prometheus_rule_group_iterations_missed_total[{W}]))",
        "group_failures": f"sum by (rule_group) (increase(prometheus_rule_evaluation_failures_total[{W}]))",
        "for_state_resets": f"topk(50, changes(ALERTS_FOR_STATE[{W}]))",
        "queue_time_p90_peak": f'max(max_over_time(prometheus_engine_query_duration_seconds{{slice="queue_time",quantile="0.9"}}[{W}]))',
        "query_slots_peak": f"max(max_over_time(prometheus_engine_queries[{W}]) / prometheus_engine_queries_concurrent_max)",
        "wal_fsync_p99_peak": f'max(max_over_time(prometheus_tsdb_wal_fsync_duration_seconds{{quantile="0.99"}}[{W}]))',
        "wal_size": "max(prometheus_tsdb_wal_storage_size_bytes)", "blocks_size": "max(prometheus_tsdb_storage_blocks_bytes)",
        "head_series": "max(prometheus_tsdb_head_series)", "samples_rate": "sum(rate(prometheus_tsdb_head_samples_appended_total[5m]))",
        "replay_seconds": "max(prometheus_tsdb_data_replay_duration_seconds)",
        "lowest_ts_age_days": "(time() - min(prometheus_tsdb_lowest_timestamp_seconds)) / 86400", "retention_seconds": "max(prometheus_tsdb_retention_limit_seconds)", "retention_bytes": "max(prometheus_tsdb_retention_limit_bytes)",
        "restarts": f"max(changes(process_start_time_seconds{{job=~\".*prometheus.*\"}}[{W}]))",
        "rss": "max(process_resident_memory_bytes{job=~\".*prometheus.*\"})", "cpu_cores_peak": f"max(max_over_time(rate(process_cpu_seconds_total{{job=~\".*prometheus.*\"}}[5m])[{W}:5m]))",
        "compactions_failed": f"max(increase(prometheus_tsdb_compactions_failed_total[{W}]))", "checkpoint_failed": f"max(increase(prometheus_tsdb_checkpoint_creations_failed_total[{W}]))",
        "wal_writes_failed": f"max(increase(prometheus_tsdb_wal_writes_failed_total[{W}]))", "wal_corruptions": "max(prometheus_tsdb_wal_corruptions_total)",
        "reloads_min_5m": f"min(min_over_time(increase(prometheus_tsdb_reloads_total[5m])[{W}:5m]))",
        "notify_errors": f"sum(increase(prometheus_notifications_errors_total[{W}]))", "notify_dropped": f"sum(increase(prometheus_notifications_dropped_total[{W}]))", "notify_queue_ratio_peak": f"max(max_over_time(prometheus_notifications_queue_length[{W}])) / max(prometheus_notifications_queue_capacity)",
        "am_discovered": "min(prometheus_notifications_alertmanagers_discovered)",
        # Which supporting data sources exist at all
        "has_self_metrics": "count(prometheus_tsdb_head_series)", "has_am_metrics": "count(alertmanager_notifications_total)",
        "has_cadvisor": "count(container_cpu_cfs_periods_total)", "has_ksm": "count(kube_pod_container_status_restarts_total)", "has_kubelet_volume": "count(kubelet_volume_stats_available_bytes)", "has_node_exporter": "count(node_filesystem_avail_bytes)", "has_blackbox": "count(probe_success)",
        "throttling_ratio_peak": f'max(max_over_time((rate(container_cpu_cfs_throttled_periods_total{{container=~"prometheus"}}[5m]) / rate(container_cpu_cfs_periods_total{{container=~"prometheus"}}[5m]))[{W}:5m]))',
        "mem_ratio_peak": f'max(max_over_time((container_memory_working_set_bytes{{container="prometheus"}} / (container_spec_memory_limit_bytes{{container="prometheus"}} > 0))[{W}:5m]))',
    }
    res = {k: q(v) for k, v in peaks.items()}
    save("peaks", {"range": W, "exprs": peaks, "results": res})

    # Map each metric used by an alert to the jobs that expose it.
    rules, metrics = fetch("_rules_again", "/api/v1/rules") or {}, set()
    try: os.remove(os.path.join(a.out, "_rules_again.json"))
    except OSError: pass
    KW = {"sum","min","max","avg","count","by","on","ignoring","group_left","group_right","without","and","or","unless","bool","offset","rate","irate","increase","delta","idelta","time","vector","absent","absent_over_time","topk","bottomk","quantile","stddev","stdvar","count_values","histogram_quantile","label_replace","label_join","clamp","clamp_min","clamp_max","changes","resets","timestamp","sort","sort_desc","abs","ceil","floor","round","exp","ln","log2","log10","sqrt","scalar","deriv","predict_linear","holt_winters","double_exponential_smoothing","sum_over_time","avg_over_time","min_over_time","max_over_time","count_over_time","last_over_time","present_over_time","quantile_over_time","stddev_over_time","stdvar_over_time","e","inf","nan","start","end","hour","minute","month","year","day_of_month","day_of_week","day_of_year","days_in_month"}
    for g in rules.get("groups", []):
        for r in g.get("rules", []):
            if r.get("type") == "alerting":
                body = re.sub(r'"[^"]*"|\'[^\']*\'|\{[^}]*\}|\[[^\]]*\]', " ", r.get("query", ""))
                metrics |= {m for m in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", body) if m not in KW}
    jobs = {}
    for m in sorted(metrics)[:800]:
        r = q(f'count by (job) ({{__name__="{m}"}})')
        jobs[m] = {x["metric"].get("job", ""): float(x["value"][1]) for x in (r or [])}
    save("metric_jobs", jobs)

    if a.am:
        for name, path in {"am_status": "/api/v2/status", "am_alerts": "/api/v2/alerts?active=true&silenced=false&inhibited=false", "am_silences": "/api/v2/silences"}.items():
            try:
                save(name, get(a.am, path, atok))
            except Exception as e:
                errors[name] = str(e)[:300]
    save("errors", errors)
    print(json.dumps({"out": a.out, "metrics_mapped": len(jobs), "errors": errors}, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
