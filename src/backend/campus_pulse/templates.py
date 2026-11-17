"""Reviewed, public-only templates for the first online workbench release."""

DATASET_SNAPSHOT_ID = (
    "99cf3df5dafe595438d7b352e3ef4137e2ab88ebda7233e898c96a4bf0d98ee6"
)
ANALYSIS_SNAPSHOT_ID = (
    "b99df3cb436403efb9961cf4f4d03e82f3ad55fd3d27994f1c6a55066abae365"
)
POPULATION_RELEASE_ID = "persona-release-v1-reviewed-17x3"


REVIEWED_SCENARIO_TEMPLATES = (
    {
        "template_key": "tongzhou_governance_notice_v2",
        "name": "通州校区治理信息冲击",
        "description": (
            "治理系统直接发布的广泛信息冲击，用于检查课程、通勤、住宿、"
            "学生组织和反馈责任如何被不同代表性人群解释。"
        ),
        "shock_origin": "governance_action",
        "phases": [
            {
                "phase_id": "baseline",
                "label": "基线",
                "window": "公告前24小时",
            },
            {
                "phase_id": "burst",
                "label": "爆发",
                "window": "公告后0–1小时",
            },
            {
                "phase_id": "spread",
                "label": "扩散",
                "window": "公告后1–24小时",
            },
            {
                "phase_id": "decay",
                "label": "衰减",
                "window": "公告后24–72小时",
            },
        ],
        "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
        "claim_boundary": (
            "历史聚合形状支持冲击阶段校准；治理分支仅为模型条件下压力测试，"
            "不是观察数据识别的因果政策效果。"
        ),
    },
    {
        "template_key": "lecture_external_incident_v2",
        "name": "讲座争议外生事件",
        "description": (
            "事件发生在论坛系统之外，少数人先期接触并在论坛扩散，"
            "用于检验同一代表性人群不会复用治理公告反应脚本。"
        ),
        "shock_origin": "external_event",
        "phases": [
            {
                "phase_id": "baseline",
                "label": "基线",
                "window": "事件前",
            },
            {
                "phase_id": "burst",
                "label": "早期出现",
                "window": "少数人先期知晓",
            },
            {
                "phase_id": "spread",
                "label": "扩散",
                "window": "讨论进入论坛",
            },
            {
                "phase_id": "decay",
                "label": "衰减",
                "window": "热度回落",
            },
        ],
        "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
        "claim_boundary": (
            "该事件用于跨冲击机制检验，不被描述为统计意义上的随机实验、"
            "实时预测或治理干预效果。"
        ),
    },
)

# The Century Gym demo is a separate reviewed scenario family.  Keeping it
# outside the generic release prevents a project named for the gym from being
# bootstrapped with the Tongzhou/lecture stories.
CENTURY_GYM_SCENARIO_TEMPLATES = (
    {
        "template_key": "century_gym_ghost_booking_v1",
        "name": "世纪馆“幽灵预约”争议",
        "description": (
            "现场场地空置，但预约系统持续显示不可预约；用于比较自然演化与"
            "证据公开、异常释放工单及跨群触达的组合治理。"
        ),
        "shock_origin": "external_event",
        "phases": [
            {"phase_id": "baseline", "label": "基线", "window": "Tick 0–2"},
            {"phase_id": "burst", "label": "出现", "window": "Tick 3–4"},
            {"phase_id": "spread", "label": "扩散", "window": "Tick 5–8"},
            {"phase_id": "decay", "label": "回落", "window": "Tick 9–10"},
        ],
        "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
        "claim_boundary": (
            "世纪馆情景是模型条件下的治理压力测试，不代表现实预约系统的"
            "因果结论或实际运行状态。"
        ),
    },
)


REVIEWED_POLICY_TEMPLATES = (
    {
        "template_key": "natural_evolution_v2",
        "name": "自然演化",
        "description": "只观察事件后的自然诉求演化，不追加治理承诺。",
        "is_baseline": True,
        "actions": [],
        "evaluation_level": "model_conditional",
    },
    {
        "template_key": "combined_governance_v2",
        "name": "组合治理",
        "description": (
            "提供可执行但仍不完整的课程、通勤、住宿、学生组织和反馈服务契约。"
        ),
        "is_baseline": False,
        "actions": [
            {
                "action_id": "course_continuity",
                "label": "课程连续性与可逆选择",
                "commitment": (
                    "退补选截止前冻结课程校区与上课时间，冲突者可专项退补选"
                    "或等效线上参与。"
                ),
            },
            {
                "action_id": "commute_service",
                "label": "通勤服务与调整窗口",
                "commitment": (
                    "住宿选择截止前公布通勤服务时段、上车方式和预计耗时，"
                    "并保留调整窗口。"
                ),
            },
            {
                "action_id": "organization_flexibility",
                "label": "组织参与弹性",
                "commitment": "学生组织成员可调班、线上参与或无责退出。",
            },
            {
                "action_id": "feedback_traceability",
                "label": "反馈责任与可查进度",
                "commitment": (
                    "FAQ标记已确认与待确认，公开负责人、更新时间和反馈处理进度。"
                ),
            },
        ],
        "evaluation_level": "model_conditional",
    },
)

CENTURY_GYM_POLICY_TEMPLATES = (
    REVIEWED_POLICY_TEMPLATES[0],
    {
        "template_key": "century_gym_combined_governance_v1",
        "name": "世纪馆组合治理",
        "description": "公开预约证据，建立异常释放工单，并向相关使用者跨群触达。",
        "is_baseline": False,
        "actions": [
            {
                "action_id": "booking_evidence_card",
                "label": "预约证据卡",
                "commitment": "公开时段状态、取消与签到记录的可核查说明。",
            },
            {
                "action_id": "stale_slot_release_ticket",
                "label": "异常释放工单",
                "commitment": "对过期、取消或未签到预约建立限时复核与释放工单。",
            },
            {
                "action_id": "cross_group_outreach",
                "label": "跨群触达",
                "commitment": "向受影响场馆使用者同步处理进度、责任人与反馈入口。",
            },
        ],
        "evaluation_level": "model_conditional",
    },
)
