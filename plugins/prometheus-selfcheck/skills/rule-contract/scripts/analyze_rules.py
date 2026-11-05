#!/usr/bin/env python3
"""Per-alert timing contract check from a collect.py snapshot. Standard library only.

  python3 analyze_rules.py ./selfcheck-data [--target-latency 600] [--tolerate 1] [--rule-files 'rules/*.yml'] [--json out.json]

Expressions are parsed with regular expressions, so complex rules are flagged for human review instead of guessed.
Formulas (validated by experiment on 2.51.2 and 3.13.5, see the repo's EVIDENCE.md):
  actual interval  I = ceil(d / S) * S, at least S     S = scrape_interval, d = peak scrape duration
  range            no sufficient offset: R >= (2 + k) * I + d;  O >= d: R >= (2 + k) * I   k = scrapes to tolerate
  lookback         I + d < lookback delta, for selectors without a range
  evaluation       T <= E, actual evaluation spacing E' = ceil(T / E) * E
  latency bound    I + max(O, d) + E' + T + F
"""
import argparse, glob, json, math, os, re, sys
UNIT = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
def dur(s):
    return sum(float(n) * UNIT[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|[smhdwy])", s or ""))
def load(d, n):
    try:
        return json.load(open(os.path.join(d, n + ".json")))
    except (OSError, ValueError):
        return None
def fmt(x):
    if x is None: return "?"
    x = float(x)
    return f"{x/3600:.1f}h" if x >= 7200 else f"{x/60:.1f}m" if x >= 120 else f"{x:.0f}s" if x >= 10 else f"{x:.2f}s"
SEL = re.compile(r'(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)?\s*(?P<lab>\{[^}]*\})?\s*(?P<rng>\[[^\]]*\])?(?:\s+offset\s+(?P<off>-?[0-9a-z]+))?')
FUNCS = {"sum","min","max","avg","count","by","on","ignoring","group_left","group_right","without","and","or","unless","bool","offset","rate","irate","increase","delta","idelta","time","vector","absent","absent_over_time","topk","bottomk","quantile","stddev","stdvar","count_values","histogram_quantile","label_replace","label_join","clamp","clamp_min","clamp_max","changes","resets","timestamp","sort","sort_desc","abs","ceil","floor","round","exp","ln","log2","log10","sqrt","scalar","deriv","predict_linear","holt_winters","double_exponential_smoothing","sum_over_time","avg_over_time","min_over_time","max_over_time","count_over_time","last_over_time","present_over_time","quantile_over_time","stddev_over_time","stdvar_over_time","e","inf","nan","start","end","hour","minute","month","year","day_of_month","day_of_week","day_of_year","days_in_month"}
def strip_label_lists(q):
    """Labels in by(), on() and similar clauses are not metrics."""
    return re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", q)
def selectors(q):
    q = strip_label_lists(q)
    out, clean = [], re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: m.group(0).replace("(", " ").replace(")", " "), q)
    for m in SEL.finditer(clean):
        n, lab, rng = m.group("name"), m.group("lab"), m.group("rng")
        if not n and not lab: continue
        if n in FUNCS and not lab and not rng: continue
        if n and re.fullmatch(r"\d.*", n): continue
        after = clean[m.end():m.end()+1]
        if n and not lab and not rng and after == "(": continue
        sub = rng and ":" in rng
        out.append({"metric": n, "labels": lab or "", "range": dur(rng[1:-1].split(":")[0]) if rng else None, "subquery": bool(sub), "offset": dur(m.group("off")) if m.group("off") else 0})
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
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--target-latency", type=float); ap.add_argument("--tolerate", type=int, default=1); ap.add_argument("--json"); ap.add_argument("--rule-files", action="append", default=[], help="glob of rule files, used to read group query_offset")
    a = ap.parse_args(); d = a.dir
    rules, targets, peaks, mj, flags = load(d, "rules"), load(d, "targets"), load(d, "peaks"), load(d, "metric_jobs") or {}, load(d, "flags") or {}
    if not rules: sys.exit("rules.json not found; run collect.py first")
    R = (peaks or {}).get("results", {})
    pv = lambda k, lab: {x["metric"].get(lab, ""): float(x["value"][1]) for x in (R.get(k) or [])}
    dpeak, gpeak, gmiss = pv("scrape_duration_peak", "job"), pv("group_duration_peak", "rule_group"), pv("group_missed", "rule_group")
    S = {}
    for t in (targets or {}).get("activeTargets", []):
        j = t["labels"].get("job", ""); s = dur(t.get("scrapeInterval", "")); S[j] = max(S.get(j, 0), s)
    lookback = dur(flags.get("query.lookback-delta", "5m")) or 300; grace = dur(flags.get("rules.alert.for-grace-period", "10m")) or 600
    O_files = group_offsets(a.rule_files); yml = (load(d, "config") or {}).get("yaml", "")
    mo = re.search(r"rule_query_offset:\s*['\"]?([0-9a-z]+)", yml); O_global = dur(mo.group(1)) if mo else 0
    offset_blind = version_ge((load(d, "buildinfo") or {}).get("version", ""), 2, 53) and not a.rule_files
    k = a.tolerate; rows = []; recorded = {r["name"] for g in rules["groups"] for r in g["rules"] if r.get("type") == "recording"}
    for g in rules["groups"]:
        E = float(g.get("interval") or 0); O_group = O_files.get(g.get("name"), dur(str(g.get("queryOffset") or "")) or O_global)
        key = f'{g.get("file")};{g.get("name")}'; T = gpeak.get(key, float(g.get("evaluationTime") or 0))
        Ep = max(E, math.ceil(T / E) * E) if E else None
        for r in g["rules"]:
            if r.get("type") != "alerting": continue
            q = r.get("query", ""); F = float(r.get("duration") or 0); sels = selectors(q); issues = []; srcs = []
            for s in sels:
                m = s["metric"]; lab = s["labels"]
                job = re.search(r'job\s*=\s*"([^"]+)"', lab); jre = re.search(r'job\s*=~\s*"([^"]+)"', lab)
                jobs = []
                if m in mj: jobs = list(mj[m])
                if job: jobs = [job.group(1)]
                elif jre:
                    jobs = [j for j in (jobs or list(S)) if re.fullmatch(jre.group(1), j)]
                srcs.append({**s, "jobs": jobs, "recorded": m in recorded})
            selfmeta = re.fullmatch(r"\s*vector\s*\(.*\)\s*", q) is not None
            missing = [s["metric"] for s in srcs if s["metric"] and s["metric"] in mj and not mj[s["metric"]] and not s["recorded"] and not s["metric"].startswith(("ALERTS",))]
            if missing and "absent" not in q: issues.append(("WARN", f"查不到 series：{', '.join(sorted(set(missing)))}。可能是名稱打錯或 target 消失，也可能是平常不存在的 metric，例如錯誤計數器"))
            worst = {"I": None, "d": None, "S": None}
            for s in srcs:
                for j in s["jobs"]:
                    if j not in S or j not in dpeak: continue
                    sc, dd = S[j], dpeak[j]; I = max(sc, math.ceil(dd / sc) * sc) if sc else None
                    if I and (worst["I"] is None or I + dd > worst["I"] + worst["d"]): worst = {"I": I, "d": dd, "S": sc, "job": j}
                    O = s["offset"] + O_group
                    if s["range"] is not None and not s["subquery"] and I:
                        need = (2 + k) * I + (0 if O >= dd else dd); base = 2 * I + (0 if O >= dd else dd)
                        if s["range"] < base: issues.append(("FAIL", f'{s["metric"]}[{fmt(s["range"])}] 太短：job {j} 的 I={fmt(I)}、d={fmt(dd)}、O={fmt(O)}，至少要 {fmt(base)}，否則視窗內不足 2 個 sample，rate 會回傳空、for 重置'))
                        elif s["range"] < need: issues.append(("WARN", f'{s["metric"]}[{fmt(s["range"])}] 餘裕不足：要容忍 {k} 次 scrape 失敗需要 {fmt(need)}'))
                    if s["range"] is None and I and not s["recorded"] and I + dd >= lookback:
                        issues.append(("FAIL", f'{s["metric"]} 沒有 range，job {j} 的 I + d = {fmt(I + dd)} 已達 lookback {fmt(lookback)}，會間歇回傳空'))
                    elif s["range"] is None and I and not s["recorded"] and I + dd >= 0.8 * lookback:
                        issues.append(("WARN", f'{s["metric"]} 沒有 range，job {j} 的 I + d = {fmt(I + dd)} 接近 lookback {fmt(lookback)}'))
                    if dd > sc: issues.append(("FAIL", f'job {j} 的 scrape 尖峰 {fmt(dd)} 超過 interval {fmt(sc)}，實際間隔被拉長到約 {fmt(I)}'))
                    if O < dd and dd > 0.5 * sc: issues.append(("WARN", f'job {j} 的 scrape 延遲 {fmt(dd)} 明顯，offset 只有 {fmt(O)}，rule 可能看不到最新資料，rate 也可能被低估'))
            if E and T > E: issues.append(("FAIL", f'group 尖峰耗時 {fmt(T)} 超過 interval {fmt(E)}，實際約每 {fmt(Ep)} 才評估一次'))
            elif E and T > 0.8 * E: issues.append(("WARN", f'group 尖峰耗時 {fmt(T)} 超過 interval {fmt(E)} 的 80%'))
            if gmiss.get(key, 0) > 0: issues.append(("WARN", f'group 期間內跳過 {gmiss[key]:.0f} 次 evaluation'))
            if F == 0 and not selfmeta and worst["I"]: issues.append(("WARN", "for 為 0，一次 scrape 或 evaluation 抖動就可能觸發或解除"))
            if F and worst["I"] and F < 2 * worst["I"]: issues.append(("WARN", f'for {fmt(F)} 小於 2 次實際 scrape（{fmt(2 * worst["I"])}），實際上只涵蓋 1–2 個點'))
            if 0 < F < grace: issues.append(("INFO", f'for {fmt(F)} 小於 grace period {fmt(grace)}，Prometheus 重啟後 for 從零計時'))
            review = any(s["recorded"] for s in srcs) or any(s["subquery"] for s in srcs) or (not srcs and not selfmeta) or (not any(s["jobs"] for s in srcs) and not selfmeta and not missing)
            if review: issues.append(("INFO", "含 recording rule、subquery，或對應不到 job，延遲要人工往上游追"))
            bound = None
            if worst["I"] and E:
                O_used = max([s["offset"] + O_group for s in srcs] or [0]); bound = worst["I"] + max(O_used, worst["d"]) + Ep + T + F
                if a.target_latency and bound > a.target_latency: issues.append(("WARN", f'偵測延遲上限約 {fmt(bound)}，超過目標 {fmt(a.target_latency)}'))
            sev = "FAIL" if any(i[0] == "FAIL" for i in issues) else "WARN" if any(i[0] == "WARN" for i in issues) else "OK"
            seen = []; [seen.append(i) for i in issues if i not in seen]
            rows.append({"alert": r["name"], "group": g.get("name"), "file": g.get("file"), "state": r.get("state"), "health": r.get("health"), "lastError": r.get("lastError", ""), "F": F, "E": E, "T": T, "S": worst["S"], "I": worst["I"], "d": worst["d"], "latency_bound": bound, "severity": sev, "issues": seen, "expr": q})
    order = {"FAIL": 0, "WARN": 1, "OK": 2}; rows.sort(key=lambda x: (order[x["severity"]], x["group"] or "", x["alert"]))
    c = {s: sum(r["severity"] == s for r in rows) for s in order}
    if offset_blind: print("> 注意：Prometheus API 不回傳 group 的 query_offset。若有使用，請加 --rule-files 'rules/*.yml'，否則結果可能誤判。\n")
    print(f"# Rule 契約檢查\n\n共 {len(rows)} 條 alert：FAIL {c['FAIL']}、WARN {c['WARN']}、OK {c['OK']}（尖峰範圍 {(peaks or {}).get('range', '?')}）\n")
    print("| 結果 | Alert | Group | S | d 尖峰 | I | E | T 尖峰 | for | 偵測延遲上限 |\n|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['severity']} | {r['alert']} | {r['group']} | {fmt(r['S'])} | {fmt(r['d'])} | {fmt(r['I'])} | {fmt(r['E'])} | {fmt(r['T'])} | {fmt(r['F'])} | {fmt(r['latency_bound'])} |")
    print("\n## 問題明細\n")
    for r in rows:
        if r["issues"]:
            print(f"### {r['severity']} {r['alert']}\n`{r['expr']}`")
            for s, t in r["issues"]: print(f"- [{s}] {t}")
            print()
    if a.json: json.dump(rows, open(a.json, "w"), indent=1, ensure_ascii=False)
if __name__ == "__main__":
    main()
