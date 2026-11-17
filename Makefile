# YuLan-OneSim Makefile

IMAGE_NAME ?= ptss/yulan-onesim
CONTAINER_NAME ?= yulan-onesim
TAG ?= latest
CAMPUS_PULSE_ONLINE_PYTHON ?= python3
CAMPUS_PULSE_IMAGE ?= campus-pulse-workbench
CAMPUS_PULSE_CONTAINER ?= campus-pulse-workbench
CAMPUS_PULSE_RUNTIME_ADAPTER ?= disabled
CAMPUS_PULSE_LIVE_LLM_ENABLED ?= false
GOVERNANCE_V2_PRESERVE ?= \
	--preserve _release/campus-pulse-competition-final.zip \
	--preserve _release/campus-pulse-competition-rc.zip

.PHONY: help build run stop clean logs shell test \
	campus-pulse-test campus-pulse-online-test campus-pulse-build e2e \
	campus-pulse-verify campus-pulse-dev campus-pulse-api-dev \
	campus-pulse-demo-check campus-pulse-demo campus-pulse-package \
	campus-pulse-runtime-fixture-once \
	campus-pulse-live-status campus-pulse-live-install \
	campus-pulse-container-context-check \
	campus-pulse-container-static-check \
	campus-pulse-container-build campus-pulse-container-run \
	campus-pulse-container-run-fixture \
	campus-pulse-container-stop \
	campus-pulse-governance-arena-test \
	campus-pulse-governance-arena-build \
	campus-pulse-governance-arena-v2-test \
	campus-pulse-governance-arena-v2-build \
	campus-pulse-governance-arena-v2-package \
	campus-pulse-governance-arena-v2-release

# 默认目标
help:
	@echo "🚀 YuLan-OneSim Docker 操作命令："
	@echo ""
	@echo "  build     - 构建Docker镜像"
	@echo "  run       - 启动容器"
	@echo "  stop      - 停止容器"
	@echo "  restart   - 重启容器"
	@echo "  logs      - 查看日志"
	@echo "  shell     - 进入容器Shell"
	@echo "  clean     - 清理容器和镜像"
	@echo "  test      - 测试容器是否正常工作"
	@echo "  campus-pulse-test   - 运行CampusPulse完整测试"
	@echo "  campus-pulse-online-test - 运行在线治理工作台测试"
	@echo "  campus-pulse-build  - 构建CampusPulse比赛前端"
	@echo "  e2e       - 运行端到端测试"
	@echo "  campus-pulse-verify - 测试并构建CampusPulse交付版本"
	@echo "  campus-pulse-dev    - 启动CampusPulse前端开发服务器"
	@echo "  campus-pulse-api-dev - 启动内部单节点工作台API"
	@echo "  campus-pulse-demo-check - 检查离线主演示包与本地HTTP回退"
	@echo "  campus-pulse-demo   - 构建并启动无需API的离线主演示"
	@echo "  campus-pulse-package - 生成并复核独立参赛RC交付ZIP"
	@echo "  campus-pulse-governance-arena-test - 验证治理干预、扩散与博弈"
	@echo "  campus-pulse-governance-arena-build - 生成零Provider治理竞技场结果"
	@echo "  campus-pulse-governance-arena-v2-release - 测试、构建并打包千体混合驱动 v2"
	@echo ""
	@echo "使用示例："
	@echo "  make build && make run"

# 构建镜像
build:
	@echo "🔨 构建 YuLan-OneSim 镜像..."
	docker build -t $(IMAGE_NAME):$(TAG) .

# 启动容器
run:
	@echo "🚀 启动 YuLan-OneSim 容器..."
	docker run -d --name $(CONTAINER_NAME) -p 8000:80 -v ./config:/app/config $(IMAGE_NAME):$(TAG)
	@echo "✅ 容器已启动！"
	@echo "🌐 Web界面: http://localhost:8000"
	@echo "📚 API文档: http://localhost:8000/docs"

# 停止容器
stop:
	@echo "🛑 停止容器..."
	-docker stop $(CONTAINER_NAME)
	-docker rm $(CONTAINER_NAME)
	@echo "✅ 容器已停止"

# 重启容器
restart: stop run

# 查看日志
logs:
	@echo "📄 查看容器日志..."
	docker logs -f $(CONTAINER_NAME)

# 进入Shell
shell:
	@echo "💻 进入容器Shell..."
	docker exec -it $(CONTAINER_NAME) bash

# 清理
clean: stop
	@echo "🧹 清理镜像..."
	-docker rmi $(IMAGE_NAME):$(TAG)
	@echo "✅ 清理完成"

# 测试
test:
	@echo "🧪 测试容器功能..."
	@sleep 5
	@if curl -f http://localhost:8000/health > /dev/null 2>&1; then \
		echo "✅ 容器运行正常"; \
	else \
		echo "❌ 容器运行异常"; \
		exit 1; \
	fi

# 一键部署（构建+运行+测试）
deploy: build run
	@echo "⏳ 等待服务启动..."
	@sleep 10
	@make test
	@echo "🎉 部署完成！"

# CampusPulse release verification
campus-pulse-test:
	python3 -m unittest discover -s src/envs/campus_pulse_governance/tests -v

campus-pulse-online-test:
	PYTHONPATH=src $(CAMPUS_PULSE_ONLINE_PYTHON) -c \
		"import fastapi, httpx, pydantic; print('CampusPulse API runtime ready')"
	PYTHONPATH=src $(CAMPUS_PULSE_ONLINE_PYTHON) -m unittest discover \
		-s src/backend/tests -p 'test_campus_pulse_*.py' -v

campus-pulse-build:
	npm --prefix src/frontend run build

e2e:
	npm run test:e2e

campus-pulse-verify: campus-pulse-test campus-pulse-online-test campus-pulse-build

campus-pulse-dev:
	npm --prefix src/frontend run dev -- --host 0.0.0.0

campus-pulse-demo-check: campus-pulse-build
	$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/run_competition_demo.py \
		--build-dir src/frontend/dist --check

campus-pulse-demo: campus-pulse-build
	$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/run_competition_demo.py \
		--build-dir src/frontend/dist --open

campus-pulse-package: campus-pulse-demo-check
	$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/package_competition_rc.py \
		--root . \
		--output _release/campus-pulse-competition-rc.zip

campus-pulse-api-dev:
	PYTHONPATH=src CAMPUS_PULSE_BIND_HOST=127.0.0.1 \
		$(CAMPUS_PULSE_ONLINE_PYTHON) -m backend.campus_pulse_app

campus-pulse-runtime-fixture-once:
	PYTHONPATH=src CAMPUS_PULSE_RUNTIME_ADAPTER=deterministic_fake \
		CAMPUS_PULSE_RUNTIME_ONCE=true \
		$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/run_runtime_worker.py

campus-pulse-live-status:
	PYTHONPATH=src CAMPUS_PULSE_LIVE_LLM_ENABLED=$(CAMPUS_PULSE_LIVE_LLM_ENABLED) \
		$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/manage_live_runtime.py status

campus-pulse-live-install:
	PYTHONPATH=src CAMPUS_PULSE_LIVE_LLM_ENABLED=$(CAMPUS_PULSE_LIVE_LLM_ENABLED) \
		$(CAMPUS_PULSE_ONLINE_PYTHON) \
		scripts/campus_pulse/manage_live_runtime.py \
		install-reviewed-inputs

campus-pulse-container-context-check:
	@test -f src/frontend/package.json
	@test -f src/frontend/package-lock.json
	@test -f src/frontend/index.html
	@test -f src/frontend/src/main.js
	@test -f scripts/campus_pulse/run_runtime_worker.py
	@test -f scripts/campus_pulse/manage_live_runtime.py
	@test -f src/backend/campus_pulse/data/live_runtime/persona-release-v1-reviewed-17x3.json
	@test -f src/backend/campus_pulse/data/live_runtime/persona-evidence-v1-reviewed-1000.json
	@test -f src/backend/campus_pulse/data/live_runtime/shock-benchmark-v1-reviewed-dual-shock.json
	@test -f src/backend/campus_pulse/data/live_runtime/shock-definitions-v1-reviewed-dual-shock.json
	@test -f docker/campus-pulse-nginx.conf
	@test -f docker/campus-pulse-supervisord.conf

campus-pulse-container-static-check: campus-pulse-container-context-check
	@grep -Fq 'CAMPUS_PULSE_RUNTIME_ADAPTER=disabled' Dockerfile.campus-pulse
	@grep -Fq 'CAMPUS_PULSE_LIVE_LLM_ENABLED=false' Dockerfile.campus-pulse
	@grep -Fq \
		'COPY src/frontend/package.json src/frontend/package-lock.json ./' \
		Dockerfile.campus-pulse
	@grep -Fq 'test -f index.html' Dockerfile.campus-pulse
	@grep -Fq 'test -f src/main.js' Dockerfile.campus-pulse
	@grep -Fq 'run_runtime_worker.py' Dockerfile.campus-pulse
	@grep -Fq 'manage_live_runtime.py' Dockerfile.campus-pulse
	@grep -Fq 'campus-pulse-supervisord.conf' Dockerfile.campus-pulse
	@grep -Fq 'http://127.0.0.1/ready' Dockerfile.campus-pulse
	@grep -Fq '[program:campus-pulse-api]' \
		docker/campus-pulse-supervisord.conf
	@grep -Fq '[program:campus-pulse-runtime-worker]' \
		docker/campus-pulse-supervisord.conf
	@grep -Fq '/app/scripts/campus_pulse/run_runtime_worker.py' \
		docker/campus-pulse-supervisord.conf
	@grep -Fq '[program:nginx]' docker/campus-pulse-supervisord.conf
	@grep -Fq 'location = /ready' docker/campus-pulse-nginx.conf
	@grep -Fq 'proxy_pass http://127.0.0.1:8000/ready;' \
		docker/campus-pulse-nginx.conf

campus-pulse-container-build: campus-pulse-container-static-check
	docker build -f Dockerfile.campus-pulse \
		-t $(CAMPUS_PULSE_IMAGE):$(TAG) .

campus-pulse-container-run:
	@case "$(CAMPUS_PULSE_RUNTIME_ADAPTER)" in \
		disabled|deterministic_fake) ;; \
		*) echo "Unsupported CAMPUS_PULSE_RUNTIME_ADAPTER: $(CAMPUS_PULSE_RUNTIME_ADAPTER)"; \
		   exit 2 ;; \
	esac
	@case "$(CAMPUS_PULSE_LIVE_LLM_ENABLED)" in \
		true|false) ;; \
		*) echo "Unsupported CAMPUS_PULSE_LIVE_LLM_ENABLED: $(CAMPUS_PULSE_LIVE_LLM_ENABLED)"; \
		   exit 2 ;; \
	esac
	docker run -d --name $(CAMPUS_PULSE_CONTAINER) \
		-p 127.0.0.1:8080:80 \
		-v campus-pulse-data:/var/lib/campus-pulse \
		-e CAMPUS_PULSE_RUNTIME_ADAPTER=$(CAMPUS_PULSE_RUNTIME_ADAPTER) \
		-e CAMPUS_PULSE_LIVE_LLM_ENABLED=$(CAMPUS_PULSE_LIVE_LLM_ENABLED) \
		$(CAMPUS_PULSE_IMAGE):$(TAG)

campus-pulse-container-run-fixture:
	@echo "Starting the provider-free deterministic fixture runtime."
	$(MAKE) campus-pulse-container-run \
		CAMPUS_PULSE_RUNTIME_ADAPTER=deterministic_fake

campus-pulse-container-stop:
	-docker stop $(CAMPUS_PULSE_CONTAINER)
	-docker rm $(CAMPUS_PULSE_CONTAINER)

campus-pulse-mass-sim-test:
	PYTHONPATH=.:src python3 -m unittest \
		src.envs.campus_pulse_governance.tests.test_mass_simulation \
		src.envs.campus_pulse_governance.tests.test_mass_engine \
		backend.tests.test_campus_pulse_schema_v9 \
		backend.tests.test_campus_pulse_mass_runtime -v

campus-pulse-mass-sim-build:
	PYTHONPATH=.:src python3 \
		scripts/campus_pulse/run_mass_simulation.py build-release

campus-pulse-mass-sim-package:
	PYTHONPATH=.:src python3 \
		scripts/campus_pulse/package_mass_sim_v1.py \
		--preserve _release/campus-pulse-competition-final.zip \
		--preserve _release/campus-pulse-competition-rc.zip

campus-pulse-governance-arena-test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 -m unittest \
		src.envs.campus_pulse_governance.tests.test_governance_arena -v
	cd src/frontend && node --test \
		tests/governanceArena.contract.test.mjs

campus-pulse-governance-arena-build:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 \
		scripts/campus_pulse/run_governance_arena.py

campus-pulse-governance-arena-v2-test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 -m unittest \
		src.envs.campus_pulse_governance.tests.test_governance_arena_v2 \
		backend.tests.test_campus_pulse_governance_v2_runtime \
		backend.tests.test_campus_pulse_schema_v9 -v
	cd src/frontend && node --test \
		tests/governanceArenaV2.contract.test.mjs

campus-pulse-governance-arena-v2-build:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 \
		scripts/campus_pulse/run_governance_arena_v2.py

campus-pulse-governance-arena-v2-package: \
		campus-pulse-governance-arena-v2-build campus-pulse-build
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 \
		scripts/campus_pulse/package_governance_arena_v2.py \
		$(GOVERNANCE_V2_PRESERVE)

campus-pulse-governance-arena-v2-release: \
		campus-pulse-governance-arena-v2-test \
		campus-pulse-governance-arena-v2-package
