# 工作狀態

最後更新：2026-11-05。

## 目前狀態

| 項目 | 狀態 | 說明 |
|---|---|---|
| Skill 與 plugin | 第一版 | 8 個 skill，`claude plugin validate` 通過 |
| 腳本 | 已驗證 | 2.51.2、3.13.5 實際執行；`tests/` 離線測試通過 |
| Pending、視窗矩陣、探針 | 完成 | 見 EVIDENCE 第 1、2 節 |
| 鏈路盲點 | 完成 | 見 EVIDENCE 第 3 節 |
| CPU、記憶體邊際 | 完成 | 見 EVIDENCE 第 4、5 節 |
| IOPS、磁碟滿、compaction 用完空間 | 完成 | 見 EVIDENCE 第 6 節 |
| 跨版本回退、alert 恢復 | 完成 | 見 EVIDENCE 第 7 節 |
| 版本資源比較（2.51.2、2.55.1、3.13.5） | 進行中 | |
| Blackbox 探測在各故障下的結果 | 進行中 | |
| K8s 驗證（minikube + kube-prometheus-stack） | 排隊中 | `container`、kube-state-metrics 相關規則尚未實際執行 |

## 尚未驗證

- `meta-alerts.yml` 只通過 `promtool check rules`，這份正式版還沒有實際跑過；實驗驗證的是同類規則。
- Reload ticker 修正版本身（PR #20161）沒有測試，還沒有正式版。
- 大型環境（遠大於 30 萬 series）的 replay 時間與記憶體尖峰。
- Native histogram、大型 block、非正常關機後的跨版本切換。
- Skill 只有 `policy-lint` 用實驗快照端到端跑過；完整互動流程與真實大型環境尚未試跑，歡迎回報問題。

## 實驗過程中修正過的結論

| 原本的結論 | 修正後 | 依據 |
|---|---|---|
| 磁碟滿會讓 reload 被餓死 | 分兩條路徑：突然寫滿時 reload 正常、time retention 因沒有新 block 而凍結；空間被 compaction 用完時 reload 才停止 | 真實磁碟實驗 |
| `R ≥ 4 × I` | `R ≥ 2I + d`；`O ≥ d` 時 `R ≥ 2I`；每容忍一次失敗加 I | 視窗矩陣 |
| 2.51 讀不懂 3.x 的 block | 目前所有版本都寫 `FormatV2`，實測可切換；官方仍只支援回退到 2.55 | 原始碼與實測 |
| Alert 恢復只查一次是重要改善 | 3 萬實例只差不到 1 秒 | 實測 |
| 第一次 IOPS 實驗沒有影響 | 限速未確認作用到寫入；重跑加探測後，受影響的是 fsync 與 compaction | `iops2` |

## 已知問題

- PromQL 用正規表示式解析，複雜表達式可能判斷不準，所以會標記人工確認。
- Prometheus API 不回傳 group 的 `query_offset`，要用 `--rule-files` 提供 rule 檔。
- `SCRAPE_NOT_MONITORED` 只看同一台 Prometheus 的 alert，其他 Prometheus 負責的 job 要用豁免。
- `lab/` 的 IOPS 情境假設 `8:0` 裝置與 cgroupfs。

## 下一步

1. 完成資源比較與 blackbox 實驗，更新 EVIDENCE 和 skill。
2. 完成 K8s 驗證，實際執行 `meta-alerts.yml`。
3. 依使用回饋調整 skill 的提問流程。
