# Prometheus Self-Check Skills

給 Prometheus 和 Alertmanager owner 的互動式自檢工具，用 [Claude Code](https://code.claude.com/docs) skill 實作。它會讀取你的設定、問查不到的事，然後回答：

- **這些 alert 接近極限時還能不能信？**
- **哪些 range 太短、哪些 scrape 沒監控、哪些 interval 太短？**
- **Monitor of monitor 缺了什麼？**
- **要不要升級？升級解決不了什麼？**

> Rule 回報正常，不代表 alert 正確。實驗中一條永遠成立的 alert 跑 25 分鐘都沒有 firing，而 `rule_evaluation_failures_total`、`iterations_missed_total` 都是 0，`up` 也是 1。

## 安裝

在 Claude Code 裡執行：

```text
/plugin marketplace add HahaSula/prometheus-selfcheck-skills
/plugin install prometheus-selfcheck@prometheus-selfcheck-skills
```

## 使用

先讓 Claude 連得到 Prometheus，K8s 可以先 port-forward：

```bash
kubectl -n <ns> port-forward svc/<prometheus-svc> 9090:9090
kubectl -n <ns> port-forward svc/<alertmanager-svc> 9093:9093
```

接著啟動完整流程：

```text
/prometheus-selfcheck:selfcheck http://localhost:9090
```

連不到也可以用：它會改成請你貼查詢結果或 rule 檔。

## Skill 一覽

| Skill | 回答什麼 |
|---|---|
| `selfcheck` | 入口：了解情境、串起其他項目、彙整報告 |
| `inventory` | 版本、規模、尖峰 scrape 與 rule 耗時、rule 與 target |
| `rule-contract` | 每條 alert 的時間契約與偵測延遲上限 |
| `policy-lint` | 明確的 policy 檢查，可設定門檻、豁免，也能放進 CI |
| `mom-coverage` | Monitor of monitor 缺口，並提供規則範本 |
| `capacity-margin` | CPU、記憶體、磁碟、IOPS 餘裕，與第一個會失效的東西 |
| `alertmanager` | 路由、dead man's switch、HA、通知失敗 |
| `upgrade-path` | 升級的實際好處、解決不了的事、路徑與檢查清單 |

每個 skill 都能單獨使用，例如 `/prometheus-selfcheck:policy-lint`。

## 不用 Claude 也能跑

腳本只用 Python 標準函式庫。`S=plugins/prometheus-selfcheck/skills`：

```bash
python3 $S/inventory/scripts/collect.py --prom http://localhost:9090 --am http://localhost:9093 --out ./selfcheck-data --range 7d
python3 $S/policy-lint/scripts/policy_lint.py ./selfcheck-data
python3 $S/rule-contract/scripts/analyze_rules.py ./selfcheck-data --target-latency 300
python3 $S/mom-coverage/scripts/check_coverage.py ./selfcheck-data
```

放進 CI：`policy_lint.py ./selfcheck-data --policy selfcheck-policy.json --fail-on FAIL`。

離線測試：`python3 -W ignore tests/test_scripts.py`。

## 安全與隱私

- **唯讀：** 腳本只送 HTTP GET，不改設定，不呼叫 reload、quit 或 admin API。
- **資料留在本機：** `selfcheck-data/` 可能含內部主機名、label 和設定內容。不要 commit 或公開，請加進 `.gitignore`。
- **Claude 會讀這些資料：** 蒐集到的內容會傳給你使用的 Claude 服務處理。請依公司規範判斷是否適用。
- **Token：** 用 `PROM_BEARER_TOKEN`、`AM_BEARER_TOKEN` 環境變數，不要貼進對話。

## 可信度與限制

- 主要結論來自 [`lab/`](lab/) 的故障注入實驗，數據在 [EVIDENCE.md](EVIDENCE.md)，目前進度在 [STATUS.md](STATUS.md)。
- 實驗是單機 Docker、30 萬 series。**驗證的是機制和訊號順序，門檻和時間不能直接套用**，大型環境要用自己的數字。
- 腳本用正規表示式解析 PromQL，複雜表達式會標記為要人工確認。
- 版本資訊截至 2026-11，使用前請查官方 [release cycle](https://prometheus.io/docs/introduction/release-cycle/)。

## 延伸閱讀

- [Prometheus Rule 信任邊際](https://claude.ai/artifact/LRtJeQMPTP3dqPBK2U1pGq)
- [Prometheus Retention 阻塞分析](https://claude.ai/artifact/TaarXxsj2np1YBSbvv7Wqx)

## License

[MIT](LICENSE)
