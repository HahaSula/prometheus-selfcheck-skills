# 工作狀態

最後更新：2026-11-05。

## 目前狀態

**第一版完成，所有排定的實驗都已結束。** 尚未在真實大型環境試用。

| 項目 | 狀態 | 說明 |
|---|---|---|
| Skill 與 plugin | 第一版 | 8 個 skill，`claude plugin validate` 通過 |
| 腳本 | 已驗證 | 2.51.2、3.13.5、kube-prometheus-stack；`tests/` 離線測試通過 |
| 規則範本 | 已驗證 | 24 條，2.51.2 與 3.13.5 `promtool check rules` 通過；K8s 上全部 `health=ok` |
| Pending、視窗矩陣、探針 | 完成 | EVIDENCE 第 1、2 節 |
| 鏈路盲點 | 完成 | EVIDENCE 第 3 節 |
| CPU、記憶體邊際 | 完成 | EVIDENCE 第 4、5 節 |
| IOPS、磁碟滿、compaction 用完空間 | 完成 | EVIDENCE 第 6 節 |
| 跨版本回退、alert 恢復、版本資源比較 | 完成 | EVIDENCE 第 7 節 |
| Blackbox 探測 | 完成 | EVIDENCE 第 8 節 |
| K8s 驗證 | 完成 | EVIDENCE 第 9 節 |

## 尚未驗證

- `meta-alerts.yml` 的 24 條規則只有部分實際觸發過；其餘只確認過可評估且有資料。`PrometheusMemoryNearLimit` 調整 `for` 後只用 `promtool test rules` 驗證。
- PVC 用量（`kubelet_volume_stats_*`）：minikube 沒有這個 metric，未驗證。
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
| `SERIES_NOT_FOUND` 是 FAIL | 降為 WARN：錯誤計數器等 metric 平常本來就不存在，無法和打錯名稱區分 | K8s 預設規則 49 筆 |
| 第一次 IOPS 實驗沒有影響 | 限速未確認作用到寫入；重跑加探測後，受影響的是 fsync 與 compaction | `iops2` |

## 已知問題

- PromQL 用正規表示式解析，複雜表達式可能判斷不準，所以會標記人工確認。
- Prometheus API 不回傳 group 的 `query_offset`，要用 `--rule-files` 提供 rule 檔。
- `SCRAPE_NOT_MONITORED` 只看同一台 Prometheus 的 alert，其他 Prometheus 負責的 job 要用豁免。
- `lab/` 的 IOPS 情境假設 `8:0` 裝置與 cgroupfs。

## 下一步

1. 請實際的 Prometheus owner 試用，依回饋調整提問流程。
2. 在真實大型環境確認 replay 時間與記憶體尖峰。
3. 有 reload ticker 的正式版後，再實測 compaction 失敗情境。
