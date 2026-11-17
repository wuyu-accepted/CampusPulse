# 世纪馆视频演示公开运行投影

该目录面向比赛视频中的“新建项目 → 启动模拟 → 逐时间步观察”流程。它从已审计的 `century-gym-live-v1` 运行派生，不修改原始运行或重新调用 Provider。

- `shared_baseline/T00.json`–`T02.json`：从原 Tick 3 快照内已保存的真实基线状态拆出的无公开发言时间步；
- `shared_baseline/T03.json`、`T04.json`：事件进入论坛后的共享前缀；
- `Natural/T05.json`–`T10.json`：自然演化；
- `D/T05.json`–`T10.json`：主动治理；
- `latest.json`：17 个公开快照、来源哈希和界面展示合同。

`latest.json.display_contract` 是前端与视频脚本共享的机器可读口径：演示者手动启动，界面依次播放 Tick 0–10，在 Tick 5 从共享论坛分成 Natural 与主动治理 D，默认先展示 D；播放间隔为 1.9 秒，播放期间 Provider 调用为 0，世纪馆不进入案例中心。

Tick 0–2 只展示人口装载、论坛初始化和共享基线冻结，不新增帖子、Claim、调用或证据。所有 Tick 3–10 公开语言仍逐字来自原 authorized-live-LLM 运行。
