# LINE Work Elf 接手入口

先讀 AGENTS.md → README.md → docs/PRODUCT_PLAN.md → docs/architecture.md → docs/multi-user.md。

## 產品與邊界

共用一個 LINE 官方帳號，多人各自開通、綁定工作群、收取自己的摘要。每人獨立 SQLite 與 Calendar 授權；一般私訊助理另行同意開啟。資料庫隔離不等於服務管理者無法管理檔案。不能自動讀任意私人聊天室或歷史訊息。

目前免費試用，service_plans.py 固定免費且 payments_enabled=False。沒有金流服務。保留內部 OpenRouter 免費模型與零價格上限，不得自行開付費模型。消費者教學不要寫成申請 API key 的安裝說明。

## 程式地圖

- elf.py：簽章驗證、事件路由、背景排程與模型代理。
- public_groups.py：成員註冊、每人資料庫、群組授權、帳號隔離、模型用量。
- interaction.py / onboarding.py / rich_menu.py：按鈕、卡片、輸入流程、教學。
- dm_companion.py：個人記錄、手動對話整理與重試、引用依據驗證。
- member_assistant.py：自選成員私訊助理；只允許自己的查詢工具與新增行程介面。
- member_calendar.py / member_oauth.py：獨立日曆權杖、單次票券、Cookie + PKCE、授權回呼。
- live_settings.py：取得更新後的通道入口網址。
- service_plans.py：目前免費方案；未來金流須新增驗證過的伺服器端授權來源。
- docs/getting-started.html：一般使用者教學。README.md 是開源部署者入口。

## 修改前與驗證

這份是公開發行原始碼，不包含本機實際設定、訊息、資料庫或憑證。不要啟動這份目錄的服務連到任何真人帳號。單元測試以獨立 LOCALAPPDATA、合成事件、暫存資料庫與 mock 執行，不連外。

```powershell
$env:LOCALAPPDATA = Join-Path $env:TEMP ('elf-tests-' + [guid]::NewGuid())
python -m unittest discover -s . -p "test_*.py" -q
git add <已審查的檔案>
python tools/check_public_files.py
git diff --cached --check
```

2026-10-27 修改後離線測試 193 項通過。本次新測試在 test_beta.py。LINE 卡片與實際模型測試只允許在部署者的正式維運環境，以合成內容執行；結果請看版本交接，不要自行對真實使用者發送測試訊息。

保留 actor 綁定、帳號隔離、撤回／刪除競態檢查、原文依據驗證、OAuth 單次票券與 relay allowlist。模型可選工具，但不能自行任意 URL 呼叫或以模型回覆當作操作成功。網路請求盡量不長時間持有 SQLite／成員鎖。

## 待辦

詳見 docs/PRODUCT_PLAN.md 的真人驗收與正式擴大條件。不得把 Google 正式發布狀態說成已通過驗證或授權永不失效。金流尚未選供應商、尚未收費。
