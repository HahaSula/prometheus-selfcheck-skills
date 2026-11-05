---
name: selfcheck
description: Interactive self-check for a Prometheus and Alertmanager deployment. Use when someone owns Prometheus and wants to know whether their alert rules can be trusted, what monitor-of-monitor (meta-monitoring) signals are missing, or how to review their setup. 當 Prometheus owner 想自檢設定、盤點 rule、確認 alert 能不能信任、補 monitor of monitor 時使用。
when_to_use: 自檢 Prometheus、自檢 Alertmanager、rule 可信度、alert 不觸發、pending 抖動、meta-monitoring、monitor of monitor、dead man's switch、升版評估
argument-hint: "[prometheus-url]"
---

# Prometheus 自檢

用互動方式幫 Prometheus owner 回答一個問題：**這些 rule 在系統接近極限時還能不能信？少了哪些 monitor of monitor？**

核心觀念：rule 回報正常不代表 alert 正確。Rule 能不能信，取決於一組時間不等式；不等式的兩邊距離就是邊際。

## 全程規則

- **唯讀。** 只用 HTTP GET 讀 API。不改設定、不呼叫 `/-/reload`、`/-/quit`、admin API，也不 push 任何檔案，除非使用者明確要求。
- **不要外傳資料。** 蒐集的資料可能含內部主機名和 label。留在本機，不要放進公開位置。如果使用者要分享報告，先提醒去識別化。
- **能量的就不要問。** 有 API 可用時，先跑腳本，只問查不到的事。
- **一次問一組，最多 3 題。** 每題說明為什麼要問、不知道時怎麼查。使用者不知道就記為「未知」，繼續往下，不要卡住。
- **數字要標來源。** 分清楚「量到的」、「使用者說的」、「推算的」。門檻是實驗環境的經驗值，不能當成對方環境的事實。
- **用使用者的語言回答。**

## 流程

### 第 1 步：了解情境（第一輪提問）

如果參數已提供 Prometheus URL，就跳過第 1 題。

1. **怎麼連到 Prometheus 和 Alertmanager？** 選項：能直接連（給 URL）、K8s 用 `kubectl port-forward`、不能連（使用者貼資料）。
2. **這台 Prometheus 最重要的 3 條 alert 是什麼？** 報告會優先分析它們。不知道也沒關係。
3. **事件發生後，最慢多久要收到通知？** 例如 5 分鐘。用來判斷偵測延遲是否可接受。

K8s 的連線範例：

```bash
kubectl -n <ns> port-forward svc/<prometheus-svc> 9090:9090
kubectl -n <ns> port-forward svc/<alertmanager-svc> 9093:9093
```

### 第 2 步：依序做各項檢查

依序使用這些 skill。使用者只想看其中一項時，直接跳到那一項。

| 順序 | Skill | 回答什麼 |
|---|---|---|
| 1 | `inventory` | 目前有什麼：版本、規模、尖峰耗時、rule 與 target |
| 2 | `rule-contract` | 每條 alert 的時間契約成立嗎？偵測延遲上限是多少？ |
| 3 | `policy-lint` | 哪些設定違反 policy？例如 range 太短、scrape 沒監控、interval 太短 |
| 4 | `mom-coverage` | Monitor of monitor 缺哪些訊號？ |
| 5 | `capacity-margin` | CPU、記憶體、磁碟、IOPS 還有多少餘裕？ |
| 6 | `alertmanager` | 通知路徑和 dead man's switch 正確嗎？ |
| 7 | `upgrade-path` | 要不要升級？升到哪一版？ |

使用 plugin 安裝時，它們的名稱是 `/prometheus-selfcheck:<skill>`。每完成一項，用 1 到 3 句告訴使用者最重要的發現，再問要不要繼續。

### 第 3 步：彙整報告

全部完成後，把報告寫到 `selfcheck-report.md`（本機），結構如下：

```markdown
# Prometheus 自檢報告：<名稱或環境>，<日期>

## 結論
這些 alert 目前能不能信，一到三句話。

## 環境摘要
版本、series、尖峰 scrape、尖峰 rule group、資源。標明每個數字的來源。

## 必須先處理
最多 5 項，依影響排序。每項：問題、影響哪些 alert、怎麼修、怎麼驗證修好了。

## 邊際總表
契約 | 現況 | 邊際 | 狀態

## Monitor of monitor 缺口
依優先度列出，附可用的規則。

## 建議順序
先修 rule、再補監控、再處理根因、最後升級。

## 沒有確認的部分
查不到的資料、人工假設、需要使用者確認的事。
```

## 判斷優先順序

1. **Alert 已經不會觸發：** 查不到 series、`for` 反覆重置、rate 視窗不足。這些最優先，因為有事也不會通知。
2. **沒有人知道監控壞了：** 缺外部 dead man's switch，或它只是 `vector(1)` 而沒有資料 canary。
3. **接近失效：** group 耗時比值超過 0.8、scrape 接近 interval、記憶體超過 limit 的 80%、磁碟空間不足。
4. **可觀測性與升級：** 其他監控缺口與版本規劃。

背景與實驗數據見 repo 的 `EVIDENCE.md`。
