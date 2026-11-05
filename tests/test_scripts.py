#!/usr/bin/env python3
"""Offline tests with a synthetic snapshot.  python3 tests/test_scripts.py"""
import json, os, subprocess, sys, tempfile, unittest
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "prometheus-selfcheck", "skills")
LINT = os.path.join(ROOT, "policy-lint", "scripts", "policy_lint.py")
RULES = os.path.join(ROOT, "rule-contract", "scripts", "analyze_rules.py")
COV = os.path.join(ROOT, "mom-coverage", "scripts", "check_coverage.py")
def alert(name, q, f=300, labels=None, ann=None):
    return {"type": "alerting", "name": name, "query": q, "duration": f, "labels": {"severity": "warning"} if labels is None else labels, "annotations": {"summary": "s"} if ann is None else ann, "health": "ok"}
def snapshot(d, version="2.51.2", extra_groups=(), yaml_extra=""):
    groups = [
        {"name": "ok", "file": "a.yml", "interval": 30, "evaluationTime": 1, "rules": [alert("Good", 'rate(fast_total{job="fast"}[5m]) > 1'), alert("TargetDown", "up == 0")]},
        {"name": "bad", "file": "a.yml", "interval": 30, "evaluationTime": 1, "rules": [
            alert("ShortRange", 'rate(slow_total{job="slow"}[5m]) > 1'),
            alert("WithOffset", 'rate(slow_total{job="slow"}[8m] offset 3m) > 1'),
            alert("Instant", 'slow_gauge{job="veryslow"} > 1'),
            alert("GaugeRate", 'rate(fast_gauge{job="fast"}[5m]) > 1'),
            alert("Missing", 'typo_metric{job="fast"} > 1'),
            alert("Gap", 'rate(fast_total{job="fast"}[10s]) > 1'),
            alert("NoFor", 'fast_gauge{job="fast"} > 1', f=0),
            alert("Bare", 'fast_gauge{job="fast"} > 2', labels={}, ann={}),
            alert("Resetting", 'fast_gauge{job="fast"} > 3'),
        ]},
        {"name": "heavy", "file": "b.yml", "interval": 30, "evaluationTime": 60, "rules": [{"type": "recording", "name": "x:sum", "query": "sum(fast_gauge)", "health": "ok"}, alert("CriticalHeavy", 'fast_gauge{job="fast"} > 4', labels={"severity": "critical"})]},
        *extra_groups,
    ]
    res = lambda lab, kv: [{"metric": {lab: k}, "value": [0, str(v)]} for k, v in kv.items()]
    peaks = {"range": "7d", "results": {
        "scrape_duration_peak": res("job", {"fast": 1, "slow": 120, "veryslow": 250, "prometheus": 0.1}),
        "scrape_samples_peak": res("job", {"fast": 100, "slow": 500000}),
        "group_duration_peak": res("rule_group", {"a.yml;ok": 1, "a.yml;bad": 1, "b.yml;heavy": 60}),
        "group_missed": res("rule_group", {"b.yml;heavy": 5}), "group_failures": res("rule_group", {}),
        "for_state_resets": [{"metric": {"alertname": "Resetting"}, "value": [0, "9"]}],
        "has_self_metrics": [{"value": [0, "1"]}],
    }}
    T = lambda j, s: {"labels": {"job": j}, "scrapeInterval": s, "scrapeTimeout": "10s" if j == "fast" else "29s"}
    files = {"rules": {"groups": groups}, "targets": {"activeTargets": [T("fast", "30s"), T("slow", "30s"), T("veryslow", "30s"), T("prometheus", "30s")]}, "peaks": peaks,
             "metric_jobs": {"fast_total": {"fast": 1}, "fast_gauge": {"fast": 1}, "slow_total": {"slow": 1}, "slow_gauge": {"veryslow": 1}, "typo_metric": {}, "up": {"fast": 1}},
             "flags": {"query.lookback-delta": "5m", "rules.alert.for-grace-period": "10m"}, "buildinfo": {"version": version},
             "metadata": {"fast_total": [{"type": "counter"}], "fast_gauge": [{"type": "gauge"}], "slow_total": [{"type": "counter"}]},
             "config": {"yaml": "global:\n  scrape_interval: 30s\n" + yaml_extra + "scrape_configs:\n- job_name: fast\n  sample_limit: 1000\n- job_name: slow\n- job_name: veryslow\n- job_name: prometheus\n- job_name: ghost\n"}}
    for n, o in files.items():
        json.dump(o, open(os.path.join(d, n + ".json"), "w"))
def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)
class Lint(unittest.TestCase):
    def lint(self, *extra, **kw):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d, **kw); out = os.path.join(d, "o.json")
            r = run(LINT, d, "--json", out, *[x.replace("@D", d) for x in extra])
            self.assertIn(r.returncode, (0, 1), r.stderr)
            return json.load(open(out)), r
    def has(self, v, pol, obj): return any(x["policy"] == pol and x["object"] == obj for x in v)
    def test_expected_violations(self):
        v, _ = self.lint()
        for pol, obj in [("RANGE_TOO_SHORT", "alert/ShortRange"), ("SCRAPE_INTERVAL_TOO_SHORT", "job/slow"), ("SCRAPE_TARGET_TOO_LARGE", "job/slow"), ("SCRAPE_JOB_NO_TARGETS", "pool/ghost"),
                         ("LOOKBACK_TOO_SHORT", "alert/Instant"), ("RATE_ON_NON_COUNTER", "alert/GaugeRate"), ("SERIES_NOT_FOUND", "alert/Missing"), ("RANGE_SHORTER_THAN_INTERVAL", "alert/Gap"),
                         ("FOR_ZERO", "alert/NoFor"), ("ALERT_MISSING_LABEL", "alert/Bare"), ("ALERT_MISSING_ANNOTATION", "alert/Bare"), ("ALERT_FOR_RESETTING", "alert/Resetting"),
                         ("GROUP_INTERVAL_TOO_SHORT", "group/heavy"), ("CRITICAL_ALERT_IN_HEAVY_GROUP", "alert/CriticalHeavy"), ("SCRAPE_NO_SAMPLE_LIMIT", "job/slow")]:
            self.assertTrue(self.has(v, pol, obj), f"{pol} {obj}")
    def test_no_false_positive(self):
        v, _ = self.lint()
        self.assertFalse(self.has(v, "RANGE_TOO_SHORT", "alert/Good"))
        self.assertFalse(self.has(v, "RANGE_TOO_SHORT", "alert/WithOffset"), "enough offset and range")
        self.assertFalse(self.has(v, "SCRAPE_NO_SAMPLE_LIMIT", "job/fast"))
        self.assertFalse(self.has(v, "SCRAPE_NOT_MONITORED", "job/fast"), "generic up alert covers all jobs")
    def test_job_without_up_alert(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d); r = json.load(open(os.path.join(d, "rules.json")))
            r["groups"][0]["rules"] = [alert("Good", 'rate(fast_total{job="fast"}[5m]) > 1'), alert("OnlyFast", 'up{job="fast"} == 0')]
            json.dump(r, open(os.path.join(d, "rules.json"), "w")); o = os.path.join(d, "o.json"); run(LINT, d, "--json", o); v = json.load(open(o))
            self.assertTrue(self.has(v, "SCRAPE_NOT_MONITORED", "job/slow")); self.assertFalse(self.has(v, "SCRAPE_NOT_MONITORED", "job/fast"))
    def test_query_offset_from_rule_file(self):
        grp = {"name": "qo", "file": "q.yml", "interval": 30, "evaluationTime": 1, "rules": [alert("QO", 'rate(slow_total{job="slow"}[5m]) > 1')]}
        v, r = self.lint(version="2.55.1", extra_groups=[grp]); self.assertTrue(self.has(v, "RANGE_TOO_SHORT", "alert/QO")); self.assertIn("query_offset", r.stdout)
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "q.yml"); open(f, "w").write("groups:\n  - name: qo\n    query_offset: 3m\n    rules: []\n")
            v, r = self.lint("--rule-files", f, version="2.55.1", extra_groups=[grp]); self.assertFalse(self.has(v, "RANGE_TOO_SHORT", "alert/QO")); self.assertFalse(self.has(v, "OFFSET_MISSING", "alert/QO"))
    def test_global_query_offset(self):
        grp = {"name": "qo", "file": "q.yml", "interval": 30, "evaluationTime": 1, "rules": [alert("QO", 'rate(slow_total{job="slow"}[5m]) > 1')]}
        v, _ = self.lint(version="2.55.1", extra_groups=[grp], yaml_extra="  rule_query_offset: 3m\n"); self.assertFalse(self.has(v, "RANGE_TOO_SHORT", "alert/QO"))
    def test_exemption_and_exit_code(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "p.json"); json.dump({"exempt": [{"policy": "SCRAPE_JOB_NO_TARGETS", "object": "pool/ghost", "reason": "planned"}, {"policy": "FOR_ZERO", "object": "alert/NoFor", "reason": "old", "until": "2000-01-01"}]}, open(f, "w"))
            v, r = self.lint("--policy", f, "--fail-on", "FAIL")
            self.assertEqual([x["exempt"] for x in v if x["object"] == "pool/ghost"], ["planned"])
            self.assertIsNone([x for x in v if x["object"] == "alert/NoFor" and x["policy"] == "FOR_ZERO"][0]["exempt"], "expired exemption must not apply")
            self.assertEqual(r.returncode, 1)
class DataRange(unittest.TestCase):
    def test_warns_when_data_is_shorter_than_range(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d); pk = json.load(open(os.path.join(d, "peaks.json"))); pk["results"]["lowest_ts_age_days"] = [{"value": [0, "0.01"]}]
            json.dump(pk, open(os.path.join(d, "peaks.json"), "w")); self.assertIn("實際只有", run(LINT, d).stdout)
class K8sShapes(unittest.TestCase):
    def test_label_lists_are_not_metrics_and_pools_map_to_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d)
            rules = json.load(open(os.path.join(d, "rules.json")))
            rules["groups"].append({"name": "k", "file": "k.yml", "interval": 30, "evaluationTime": 1, "rules": [alert("Agg", 'sum by (namespace, service) (rate(fast_total{job="fast"}[5m])) / ignoring (code) group_left (cluster) fast_gauge > 1')]})
            json.dump(rules, open(os.path.join(d, "rules.json"), "w"))
            t = json.load(open(os.path.join(d, "targets.json")))
            t["activeTargets"].append({"labels": {"job": "svc-a"}, "scrapePool": "serviceMonitor/ns/svc-a/0", "scrapeInterval": "30s", "scrapeTimeout": "10s"})
            json.dump(t, open(os.path.join(d, "targets.json"), "w"))
            c = json.load(open(os.path.join(d, "config.json"))); c["yaml"] += "- job_name: serviceMonitor/ns/svc-a/0\n  sample_limit: 100\n- job_name: serviceMonitor/ns/empty/0\n"
            json.dump(c, open(os.path.join(d, "config.json"), "w"))
            o = os.path.join(d, "o.json"); run(LINT, d, "--json", o); v = json.load(open(o))
            has = lambda pol, obj: any(x["policy"] == pol and x["object"] == obj for x in v)
            self.assertFalse(any(x["policy"] == "SERIES_NOT_FOUND" and x["object"] == "alert/Agg" for x in v))
            self.assertTrue(has("SCRAPE_JOB_NO_TARGETS", "pool/serviceMonitor/ns/empty/0"))
            self.assertFalse(has("SCRAPE_JOB_NO_TARGETS", "pool/serviceMonitor/ns/svc-a/0"))
            self.assertFalse(has("SCRAPE_NO_SAMPLE_LIMIT", "job/svc-a"))
class Others(unittest.TestCase):
    def test_analyze(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d); o = os.path.join(d, "o.json"); r = run(RULES, d, "--json", o); self.assertEqual(r.returncode, 0, r.stderr)
            rows = {x["alert"]: x for x in json.load(open(o))}
            self.assertEqual(rows["ShortRange"]["severity"], "FAIL"); self.assertEqual(rows["Good"]["severity"], "OK")
            self.assertEqual(rows["ShortRange"]["I"], 120); self.assertEqual(rows["Missing"]["severity"], "WARN")
            self.assertEqual(rows["Good"]["latency_bound"], 30 + 1 + 30 + 1 + 300)
    def test_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot(d); o = os.path.join(d, "o.json"); r = run(COV, d, "--json", o); self.assertEqual(r.returncode, 0, r.stderr)
            c = {x["signal"]: x["covered"] for x in json.load(open(o))}
            self.assertTrue(c["關鍵 target 消失或停機"]); self.assertFalse(c["WAL 寫入失敗"])
if __name__ == "__main__":
    unittest.main()
