---
name: policy-lint
description: Evaluate Prometheus against explicit monitoring policies and name violations directly, such as a range that is too short, a scrape job with no monitoring, or an interval that is too short, with suggested values. Supports team thresholds, exemptions, and CI. 用明確的 policy 檢查 Prometheus，直接指出 range 太短、scrape 沒監控、interval 太短，並給建議值。
argument-hint: "[snapshot-dir] [policy-file]"
---

# Policy 檢查

把「這樣寫對不對」變成可重複執行的規則：哪一條 policy、哪個對象違反、為什麼、建議改成多少。完整清單見 [references/policies.md](references/policies.md)。

## 執行

先用 `inventory` 取得資料，再執行：

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/policy_lint.py" ./selfcheck-data
```

使用團隊自己的門檻或豁免：

```bash
cp "${CLAUDE_SKILL_DIR}/references/policy.example.json" selfcheck-policy.json
python3 "${CLAUDE_SKILL_DIR}/scripts/policy_lint.py" ./selfcheck-data --policy selfcheck-policy.json
```

放進 CI 時，加上 `--fail-on FAIL`，有違反時回傳 1。

**2.53 以上如果有用 `query_offset`，一定要提供 rule 檔：** Prometheus API 不回傳 group 的 `query_offset`，否則這些 rule 會被誤判。全域的 `rule_query_offset` 會自動從設定讀取。

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/policy_lint.py" ./selfcheck-data --rule-files 'rules/*.yml'
```

## 互動

1. **先報摘要：** FAIL、WARN 數量，以及數量最多的 policy。
2. **逐類處理，FAIL 優先。** 不要一次貼完所有明細。先問使用者想從哪一類開始；對方有指定重要 alert 時，就先看那些。
3. **確認數字前提：** 建議值來自 `peaks.json` 的範圍。問：這段範圍涵蓋尖峰嗎？尖峰是常態還是偶發？
4. **每個建議都說明代價。** 例如 range 拉長會讓反應變慢、查詢變貴；interval 拉長會降低解析度；offset 會讓通知變慢。
5. **討論豁免。** 使用者認為某項不適用時，把對象、理由和到期日寫進 `selfcheck-policy.json`，不要直接略過。豁免一定要有理由，建議設到期日。

## 判讀限制

- `SERIES_NOT_FOUND` 無法分辨打錯名稱和「平常不存在的 metric」，例如錯誤計數器。kube-prometheus-stack 這類預設規則會有很多筆，先看自己寫的 rule。

- 腳本用正規表示式解析 PromQL。Recording rule 的上游延遲、複雜的 `on()`、subquery 都要人工看。報告前請讀該條表達式。
- `SCRAPE_NOT_MONITORED` 只看**這台** Prometheus 的 alert。如果另一台 Prometheus 負責，就用豁免並寫明理由，或請對方提供那台的 rule。
- 建議值是依公式算出的下限，不代表最佳值。要配合可接受的偵測延遲一起決定。
- 尖峰只包含抓到的資料，scrape 失敗的那幾次不在裡面。

## 修正後

重新執行 `inventory` 和本 skill 比較前後差異，並用 `changes(ALERTS_FOR_STATE{alertname="X"}[1h])` 確認 `for` 不再重置。
