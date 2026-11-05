#!/usr/bin/env python3
"""Evaluate a collect.py snapshot against explicit monitoring policies. Standard library only.

  python3 policy_lint.py ./selfcheck-data [--policy policy.json] [--rule-files 'rules/*.yml'] [--json out.json] [--fail-on FAIL]

Output names the policy, the object breaking it, why it matters, and a suggested value.
Exit code 1 when --fail-on is set and a violation of that severity or higher exists, so it can run in CI.
Expressions are parsed with regular expressions: complex rules are marked for review rather than guessed.
"""
import argparse, datetime, glob, json, math, os, re, sys
UNIT = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
DEFAULT = {"tolerate_missed_scrapes": 1, "scrape_ratio_warn": .8, "group_ratio_warn": .8, "large_target_samples": 200000, "for_reset_warn": 3, "required_labels": ["severity"], "required_annotations": ["summary"], "critical_severities": ["critical", "page"], "ignore_jobs": [], "exempt": []}
KW = {"sum","min","max","avg","count","by","on","ignoring","group_left","group_right","without","and","or","unless","bool","offset","rate","irate","increase","delta","idelta","time","vector","absent","absent_over_time","topk","bottomk","quantile","stddev","stdvar","count_values","histogram_quantile","label_replace","label_join","clamp","clamp_min","clamp_max","changes","resets","timestamp","sort","sort_desc","abs","ceil","floor","round","exp","ln","log2","log10","sqrt","scalar","deriv","predict_linear","holt_winters","double_exponential_smoothing","sum_over_time","avg_over_time","min_over_time","max_over_time","count_over_time","last_over_time","present_over_time","quantile_over_time","stddev_over_time","stdvar_over_time","e","inf","nan","start","end","hour","minute","month","year","day_of_month","day_of_week","day_of_year","days_in_month"}
SEL = re.compile(r'(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)?\s*(?P<lab>\{[^}]*\})?\s*(?P<rng>\[[^\]]*\])?(?:\s+offset\s+(?P<off>-?[0-9a-z]+))?')
def dur(s): return sum(float(n) * UNIT[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|[smhdwy])", s or ""))
def fmt(x):
    if x is None: return "?"
    x = float(x)
    if x >= 7200: return f"{x/3600:.1f}h"
    if x >= 120: return f"{x/60:.1f}m"
    return f"{x:.0f}s" if x >= 10 else f"{x:.2f}s"
def nice(sec):
    """Round a duration up to a value someone would write in a rule."""
    for v in (15, 30, 60, 90, 120, 180, 240, 300, 360, 480, 600, 900, 1200, 1800, 3600, 7200, 14400, 21600, 43200, 86400):
        if sec <= v: return v
    return math.ceil(sec / 3600) * 3600
def durs(sec): return f"{int(sec/3600)}h" if sec >= 3600 and sec % 3600 == 0 else f"{int(sec/60)}m" if sec >= 60 and sec % 60 == 0 else f"{int(sec)}s"
def strip_label_lists(q):
    """Labels in by(), on() and similar clauses are not metrics."""
    return re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", q)
def selectors(q):
    q = strip_label_lists(q)
    out = []
    for m in SEL.finditer(q):
        n, lab, rng = m.group("name"), m.group("lab"), m.group("rng")
        if not n and not lab: continue
        if n and re.fullmatch(r"\d.*", n): continue
        if n in KW and not lab and not rng: continue
        if n and not lab and not rng and q[m.end():m.end()+1] == "(": continue
        fn = re.search(r"([a-z_]+)\s*\(\s*$", q[:m.start()])
        out.append({"metric": n, "labels": lab or "", "range": dur(rng[1:-1].split(":")[0]) if rng else None, "subquery": bool(rng and ":" in rng), "offset": dur(m.group("off")) if m.group("off") else 0, "fn": fn.group(1) if fn else None})
    return out
def group_offsets(files):
    """The rules API does not expose a group's query_offset, so read it from rule files when provided."""
    out = {}
    for pat in files:
        for path in glob.glob(pat):
            name = None
            for line in open(path, encoding="utf8"):
                m = re.match(r"\s*-\s*name:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(#.*)?$", line)
                if m: name = m.group(1)
                m = re.match(r"\s*query_offset:\s*['\"]?([0-9a-z]+)", line)
                if m and name: out[name] = dur(m.group(1))
    return out
def version_ge(v, major, minor):
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (major, minor)
def strip_strings(q): return re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: '"' + re.sub(r"[()\[\]]", " ", m.group(0)[1:-1]) + '"', q)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--policy"); ap.add_argument("--json"); ap.add_argument("--fail-on", choices=["FAIL", "WARN"]); ap.add_argument("--rule-files", action="append", default=[], help="glob of rule files, used to read group query_offset")
    a = ap.parse_args(); P = dict(DEFAULT)
    if a.policy: P.update(json.load(open(a.policy)))
    def load(n):
        try: return json.load(open(os.path.join(a.dir, n + ".json")))
        except (OSError, ValueError): return None
    rules, targets, peaks, mj, flags, cfg, meta = load("rules"), load("targets"), load("peaks") or {}, load("metric_jobs") or {}, load("flags") or {}, load("config") or {}, load("metadata") or {}
    if not rules: sys.exit("rules.json not found; run the inventory skill first")
    R = peaks.get("results", {})
    pv = lambda k, lab: {x["metric"].get(lab, ""): float(x["value"][1]) for x in (R.get(k) or [])}
    dp, sp, gp, gm, gf = pv("scrape_duration_peak", "job"), pv("scrape_samples_peak", "job"), pv("group_duration_peak", "rule_group"), pv("group_missed", "rule_group"), pv("group_failures", "rule_group")
    resets = {}
    for x in (R.get("for_state_resets") or []):
        n = x["metric"].get("alertname", ""); resets[n] = max(resets.get(n, 0), float(x["value"][1]))
    S, TO, count, pools = {}, {}, {}, {}
    for t in (targets or {}).get("activeTargets", []):
        pools.setdefault(t.get("scrapePool", t["labels"].get("job", "")), set()).add(t["labels"].get("job", ""))
        j = t["labels"].get("job", ""); S[j] = max(S.get(j, 0), dur(t.get("scrapeInterval", ""))); TO[j] = max(TO.get(j, 0), dur(t.get("scrapeTimeout", ""))); count[j] = count.get(j, 0) + 1
    lookback, grace = dur(flags.get("query.lookback-delta", "5m")) or 300, dur(flags.get("rules.alert.for-grace-period", "10m")) or 600
    k = P["tolerate_missed_scrapes"]; V = []
    O_files = group_offsets(a.rule_files); yaml_all = cfg.get("yaml", "")
    gm_off = re.search(r"rule_query_offset:\s*['\"]?([0-9a-z]+)", yaml_all); O_global = dur(gm_off.group(1)) if gm_off else 0
    version = (load("buildinfo") or {}).get("version", "")
    offset_blind = version_ge(version, 2, 53) and not a.rule_files
    def add(pol, sev, obj, why, fix): V.append({"policy": pol, "severity": sev, "object": obj, "why": why, "fix": fix})
    I_of = lambda j: max(S[j], math.ceil(dp[j] / S[j]) * S[j]) if S.get(j) and j in dp else None
    # ---- scrape jobs ----
    allq = [strip_strings(r.get("query", "")) for g in rules["groups"] for r in g["rules"] if r.get("type") == "alerting"]
    generic_up = any(re.search(r'(?<![a-zA-Z0-9_:])up(?![a-zA-Z0-9_:])(?!\s*\{[^}]*job)', q) for q in allq)
    used = {}
    for m, jobs in mj.items():
        for j in jobs: used.setdefault(j, set()).add(m)
    yaml = cfg.get("yaml", ""); blocks = {}
    for b in re.split(r"(?m)^- job_name:\s*", yaml.split("scrape_configs:", 1)[-1])[1:]:
        name = b.split("\n", 1)[0].strip().strip("'\""); blocks[name] = b
    # job_name in the config is the scrape pool; the job label can differ after relabeling (for example ServiceMonitor).
    for pool in sorted(blocks):
        if pool not in pools and not pools.get(pool) and pool not in P["ignore_jobs"]:
            add("SCRAPE_JOB_NO_TARGETS", "WARN", f"pool/{pool}", "設定了這個 scrape pool，但目前沒有任何 active target，服務發現可能失效；依賴它的 alert 不會觸發", "檢查服務發現與 relabel，或移除這個設定")
    pool_of = {j: p for p, js in pools.items() for j in js}
    for j in sorted(S):
        if j in P["ignore_jobs"]: continue
        o = f"job/{j}"
        cov = generic_up or any(re.search(r'(?<![a-zA-Z0-9_:])up\s*\{[^}]*' + re.escape(j), q) for q in allq) or any(re.search(r'absent\w*\([^)]*' + re.escape(j), q) for q in allq)
        if not cov:
            add("SCRAPE_NOT_MONITORED", "FAIL", o, "沒有 alert 會在這個 job 的 target 停機或消失時通知你；依賴它的 alert 只會安靜地不觸發", f'新增 up{{job="{j}"}} == 0 的告警，並加 absent(up{{job="{j}"}}) 防止 target 整個消失')
        d, s = dp.get(j), S.get(j)
        if d is not None and s:
            I = I_of(j)
            if d > s: add("SCRAPE_INTERVAL_TOO_SHORT", "FAIL", o, f"尖峰 scrape {fmt(d)} 超過 interval {fmt(s)}，tick 被丟掉，實際間隔拉長到約 {fmt(I)}", f"優先降低 series 或拆 target；無法降低時，把 interval 改成至少 {durs(nice(d / P['scrape_ratio_warn']))}，讓設定反映實際")
            elif d > P["scrape_ratio_warn"] * s: add("SCRAPE_INTERVAL_TOO_SHORT", "WARN", o, f"尖峰 scrape {fmt(d)} 已超過 interval {fmt(s)} 的 {P['scrape_ratio_warn']:.0%}", f"降低 series，或把 interval 調到 {durs(nice(d / P['scrape_ratio_warn']))}")
            to = TO.get(j)
            if to and d > P["scrape_ratio_warn"] * to and d <= to: add("SCRAPE_TIMEOUT_TOO_CLOSE", "WARN", o, f"尖峰 scrape {fmt(d)} 接近 timeout {fmt(to)}，再慢一點就會 scrape 失敗", "查 target 回應時間，再決定是否調整 timeout")
        n = sp.get(j)
        if n and n > P["large_target_samples"]:
            add("SCRAPE_TARGET_TOO_LARGE", "WARN", o, f"單一 target 尖峰 {n:,.0f} 個 sample。每個 target 只有一條 goroutine，parse 與 append 受單核速度限制，加 CPU 幫助有限", "用 metric_relabel_configs 丟不用的 series，或拆 target")
        b = blocks.get(pool_of.get(j, j))
        if b is not None and not re.search(r"sample_limit:\s*[1-9]", b):
            add("SCRAPE_NO_SAMPLE_LIMIT", "INFO", o, "沒有 sample_limit，target 突然暴增 series 時會直接衝擊整台 Prometheus", "設定比現況略高的 sample_limit，並監控 prometheus_target_scrapes_exceeded_sample_limit_total")
        if j not in used and j not in ("", "prometheus", "alertmanager"):
            add("SCRAPE_UNUSED_BY_ALERTS", "INFO", o, "沒有任何 alert 用到這個 job 的 metric", "確認這是否符合預期；只用於 dashboard 的話可以豁免 SCRAPE_NOT_MONITORED")
    # ---- rule groups and alerts ----
    recorded = {r["name"] for g in rules["groups"] for r in g["rules"] if r.get("type") == "recording"}
    for g in rules["groups"]:
        E = float(g.get("interval") or 0); gname = g.get("name"); key = f'{g.get("file")};{gname}'; O_g = O_files.get(gname, dur(str(g.get("queryOffset") or "")) or O_global)
        T = gp.get(key, float(g.get("evaluationTime") or 0)); o = f"group/{gname}"
        if E and T > E: add("GROUP_INTERVAL_TOO_SHORT", "FAIL", o, f"尖峰耗時 {fmt(T)} 超過 interval {fmt(E)}，evaluation 會被跳過，同 group 的 alert 全部變慢", f"把昂貴的 rule 拆到別的 group，或把 interval 改成至少 {durs(nice(T / P['group_ratio_warn']))}")
        elif E and T > P["group_ratio_warn"] * E: add("GROUP_INTERVAL_TOO_SHORT", "WARN", o, f"尖峰耗時 {fmt(T)} 超過 interval {fmt(E)} 的 {P['group_ratio_warn']:.0%}", "拆出昂貴的 rule")
        if gf.get(key, 0) > 0: add("GROUP_EVAL_FAILING", "FAIL", o, f"查詢範圍內失敗 {gf[key]:.0f} 次", "查 /api/v1/rules 的 lastError")
        Ep = max(E, math.ceil(T / E) * E) if E else 0
        for r in g["rules"]:
            if r.get("health") not in (None, "ok", "unknown"): add("RULE_UNHEALTHY", "FAIL", f'rule/{r["name"]}', f'health={r.get("health")}：{r.get("lastError", "")[:120]}', "修正查詢或資料問題")
            if r.get("type") != "alerting": continue
            n, q = r["name"], strip_strings(r.get("query", "")); o = f"alert/{n}"; F = float(r.get("duration") or 0); sels = selectors(q); labels = r.get("labels", {}); ann = r.get("annotations", {})
            for lab in P["required_labels"]:
                if lab not in labels: add("ALERT_MISSING_LABEL", "WARN", o, f"缺少 label {lab}，無法正確路由", f"加上 {lab}")
            for an in P["required_annotations"]:
                if an not in ann: add("ALERT_MISSING_ANNOTATION", "INFO", o, f"缺少 annotation {an}，值班時缺乏說明", f"加上 {an}")
            if re.fullmatch(r"\s*vector\s*\(.*\)\s*", q): continue
            if resets.get(n, 0) > P["for_reset_warn"]: add("ALERT_FOR_RESETTING", "FAIL", o, f"查詢範圍內 for 計時重置 {resets[n]:.0f} 次，條件可能成立卻無法 firing（先排除 Prometheus 重啟）", "檢查 RANGE_TOO_SHORT 與 OFFSET_MISSING")
            sev_v = str(labels.get("severity", "")).lower()
            if sev_v in P["critical_severities"] and E and T > .5 * E and any(x.get("type") == "recording" for x in g["rules"]):
                add("CRITICAL_ALERT_IN_HEAVY_GROUP", "WARN", o, f"關鍵 alert 放在耗時 {fmt(T)} 的 group，資源緊張時會變慢或漏掉事件", "把它移到獨立的小 group；若依賴同 group 的 recording rule，要連帶評估")
            if F == 0: add("FOR_ZERO", "WARN", o, "沒有 for，一次抖動就觸發或解除", "除非這是刻意的立即告警，否則加上 for")
            elif F < grace: add("FOR_NOT_RESTORED_ON_RESTART", "INFO", o, f"for {fmt(F)} 小於 grace period {fmt(grace)}，重啟後從零計時", "可接受就忽略；要接續計時需要較長的 for")
            mx, seen = None, set()
            for s in sels:
                m = s["metric"]
                if m and m in mj and not mj[m] and m not in recorded and not m.startswith("ALERTS") and "absent" not in q and m not in seen:
                    seen.add(m); add("SERIES_NOT_FOUND", "WARN", o, f"目前查不到 {m}。可能是名稱打錯或 target 消失，這時 alert 永遠不會觸發；也可能是平常不存在的 metric，例如錯誤計數器", "確認 metric 名稱與來源；確定該存在的話，為來源 job 加上 absent 告警")
                if m and s["fn"] in ("rate", "irate", "increase") and isinstance(meta.get(m), list) and meta[m] and meta[m][0].get("type") not in ("counter", "histogram", "summary", "unknown", "untyped", ""):
                    add("RATE_ON_NON_COUNTER", "WARN", o, f'{s["fn"]}() 用在 {m}，它的 metadata 型別是 {meta[m][0].get("type")}', "gauge 改用 deriv() 或 delta()")
                jm = re.search(r'job\s*=\s*"([^"]+)"', s["labels"]); jobs = [jm.group(1)] if jm else list(mj.get(m, []))
                if s["range"] and E and E > s["range"] and not s["subquery"]: add("RANGE_SHORTER_THAN_INTERVAL", "WARN", o, f'{m}[{fmt(s["range"])}] 比 evaluation interval {fmt(E)} 短，視窗之間有空隙，短暫尖峰會被漏掉', f"把 range 拉長到至少 {durs(nice(E))}")
                for j in jobs:
                    I = I_of(j)
                    if not I: continue
                    d = dp[j]; O = s["offset"] + O_g; mx = max(mx or 0, I)
                    if s["range"] and not s["subquery"]:
                        base, need = 2 * I + (0 if O >= d else d), (2 + k) * I + (0 if O >= d else d)
                        if s["range"] < base:
                            add("RANGE_TOO_SHORT", "FAIL", o, f'{m}[{fmt(s["range"])}] 太短：job {j} 實際間隔 {fmt(I)}、scrape 尖峰 {fmt(d)}、offset {fmt(O)}，視窗內可能不足 2 個 sample，rate 回傳空、for 歸零', f"range 至少 {durs(nice(need))}（容忍 {k} 次 scrape 失敗）；加上足夠 offset 後可以降到 {durs(nice((2 + k) * I))}")
                        elif s["range"] < need:
                            add("RANGE_LOW_MARGIN", "WARN", o, f'{m}[{fmt(s["range"])}] 餘裕不足，job {j} 漏 {k} 次 scrape 就可能回傳空', f"range 改成 {durs(nice(need))}")
                    if O < d and d > .5 * S[j]:
                        add("OFFSET_MISSING", "WARN", o, f"job {j} scrape 尖峰 {fmt(d)}，offset 只有 {fmt(O)}，rule 看不到還沒寫入的資料，事後查詢也會對不上", f"offset 至少 {durs(nice(d))}：2.53 以上用 query_offset，舊版在每個 selector 寫 offset")
                    if s["range"] is None and m not in recorded and I + d >= lookback:
                        add("LOOKBACK_TOO_SHORT", "FAIL", o, f"{m} 沒有 range，job {j} 的 I + d = {fmt(I + d)} 達到 lookback {fmt(lookback)}，會間歇查不到", f"降低 scrape 耗時或拉長 interval 的設定；可改用 last_over_time({m}[{durs(nice(2 * I + d))}])")
            if F and mx and F < 2 * mx: add("FOR_TOO_SHORT", "WARN", o, f"for {fmt(F)} 小於 2 次實際 scrape（{fmt(2 * mx)}），實際上只涵蓋 1–2 個點", f"for 至少 {durs(nice(2 * mx))}")
    # ---- exemptions ----
    today = datetime.date.today().isoformat(); uniq, seen = [], set()
    for v in V:
        key = (v["policy"], v["object"], v["why"])
        if key in seen: continue
        seen.add(key); v["exempt"] = None
        for e in P["exempt"]:
            if e.get("policy") == v["policy"] and re.search(e.get("object", ".*"), v["object"]) and (not e.get("until") or e["until"] >= today):
                v["exempt"] = e.get("reason", "")
        uniq.append(v)
    active = [v for v in uniq if v["exempt"] is None]; order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    active.sort(key=lambda v: (order[v["severity"]], v["policy"], v["object"]))
    c = {s: sum(v["severity"] == s for v in active) for s in order}; pol = {}
    for v in active: pol[(v["severity"], v["policy"])] = pol.get((v["severity"], v["policy"]), 0) + 1
    age = R.get("lowest_ts_age_days") or []
    if age and float(age[0]["value"][1]) * 86400 < dur(peaks.get("range", "0s")):
        print(f"> 注意：TSDB 實際只有約 {fmt(float(age[0]['value'][1]) * 86400)} 的資料，短於查詢範圍 {peaks.get('range')}，尖峰可能還沒出現，建議值偏低。\n")
    if offset_blind: print("> 注意：Prometheus API 不回傳 group 的 query_offset。若有使用，請加 --rule-files 'rules/*.yml'，否則 RANGE 與 OFFSET 的結果可能誤判。\n")
    print(f"# Policy 檢查\n\nFAIL {c['FAIL']}、WARN {c['WARN']}、INFO {c['INFO']}、已豁免 {len(uniq) - len(active)}（尖峰範圍 {peaks.get('range', '?')}）\n\n## 違反的 policy\n\n| 嚴重度 | Policy | 數量 |\n|---|---|---|")
    for (s, n), x in sorted(pol.items(), key=lambda i: (order[i[0][0]], -i[1])): print(f"| {s} | {n} | {x} |")
    print("\n## 明細\n\n| 嚴重度 | Policy | 對象 | 問題 | 建議 |\n|---|---|---|---|---|")
    for v in active: print(f"| {v['severity']} | {v['policy']} | {v['object']} | {v['why']} | {v['fix']} |")
    if len(uniq) > len(active):
        print("\n## 已豁免\n\n| Policy | 對象 | 理由 |\n|---|---|---|")
        for v in uniq:
            if v["exempt"] is not None: print(f"| {v['policy']} | {v['object']} | {v['exempt']} |")
    if a.json: json.dump(uniq, open(a.json, "w"), indent=1, ensure_ascii=False)
    if a.fail_on and any(order[v["severity"]] <= order[a.fail_on] for v in active): sys.exit(1)
if __name__ == "__main__":
    main()
