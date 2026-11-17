# CampusPulse 前端

Vue 3 + Vite 前端。主演示路由为 `/campus-pulse`，包含产品封面、论坛结果、案例中心、项目工作台、世纪馆现场运行台和系统/证据配置；正式后端结果使用 `/campus-pulse?run_id=run_<24hex>`。

```bash
npm install
npm run dev
npm run test:forum-twin
npm run build
```

主要文件：

- `src/App.vue`、`src/router/index.js`：应用入口和路由；
- `src/campus-pulse/landing/`：产品封面；
- `src/campus-pulse/simulator/`：模拟器首页和产品说明；
- `src/campus-pulse/workbench/`：项目创建、模型配置、preflight、运行监控和 Agent 世界；
- `src/campus-pulse/live/`：世纪馆逐 Tick 运行台；
- `src/campus-pulse/forum/`、`src/campus-pulse/results/`：论坛、Claim、治理和案例结果组件；
- `src/campus-pulse/source/`、`src/services/`：公开结果适配、API 和哈希校验；
- `public/campus-pulse-data/`：脱敏、哈希绑定的离线公开资产；
- `tests/`、`src/**/*.test.*`：页面合同、论坛组件和 i18n 测试；
- `dist/`：本地生产构建产物，不提交。

页面读取后端或随包提供的案例记录。加载失败时显示重试入口；运行检查和完整性验证由服务与数据适配层负责。
