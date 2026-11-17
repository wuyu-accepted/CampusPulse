# 团队内部全量交接包说明

这个目录只用于 CampusPulse 团队成员之间的线下交接，不用于 GitHub、比赛提交或公开发布。内部包会在源码之外包含本机实验运行目录、`data/private/`、Provider 请求/响应材料和本地 SQLite，便于继续分析历史实验、定位运行问题和重算内部指标。

包内仍会自动排除 API Key、`API.txt`、本地凭据和会话密钥。收到压缩包后请只在受控设备上解压，不要重新上传到公开仓库、网盘公开链接或提交到 Git。

公开提交请使用不含本目录数据的源码包；内部复现实验请先阅读 `docs/EVALUATION_REPRODUCTION.md`，再按需要使用 `data/private/` 和 `_run/` 中的输入。
