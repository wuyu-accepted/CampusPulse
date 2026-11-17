# 世纪馆完整公开回放

该目录保存 `RUN-GYM-PREVIEW-R1` 的公开、只读逐时间步投影：

- `shared_baseline/T03.json`、`T04.json`：Natural 与 D 共用的论坛前缀；
- `Natural/T05.json`–`T10.json`：无新增治理动作的自然演化；
- `D/T05.json`–`T10.json`：证据卡、异常释放工单和跨项目触达进入论坛后的主动治理分支；
- `latest.json`：14 个已提交时间步、结果哈希与运行状态的 manifest。

每个 Tick 文件只包含公开合成论坛投影，不包含 Prompt、Provider body、私有 Feed、源 ID、真实身份、密钥或隐藏总体状态。`latest.json` 的每个游标绑定对应文件内部的 `content_sha256`；本目录的 `SHA256SUMS.txt` 另外绑定文件字节。

前端通过 `century-gym-replay` 会话读取该目录，不发送 Provider 请求。运行的完整结果、机制审计、中文报告和比赛故事图位于相邻 `../final/`。
