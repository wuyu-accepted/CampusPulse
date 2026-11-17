"""Pure, deterministic JSON and single-file HTML live-result reports."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import html
import json
from typing import Any, Mapping

from .live_result_contract import (
    LIVE_RESULT_CLAIM_BOUNDARY,
    LiveResultContractError,
    validate_live_aggregate_result_v2,
)
from .live_runtime_contract import (
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_LLM_EXECUTION_PROVENANCE,
)


LIVE_REPORT_SCHEMA_VERSION = "campus-pulse-live-report-document-v1"
LIVE_REPORT_KIND = "competition_rc_aggregate_report"
LIVE_REPORT_PRIVACY = {
    "publication_mode": "aggregate_only",
    "record_level_material": "excluded",
    "free_text_material": "excluded",
    "private_runtime_material": "excluded",
    "sensitive_configuration": "excluded",
}
LIVE_REPORT_COMMON_WARNING_CODES = (
    "model_conditional_simulation_only",
    "causal_effect_not_identified",
    "not_a_forecast",
    "not_a_population_estimate",
    "not_individual_truth",
    "not_real_time_monitoring",
)
LIVE_REPORT_FIXTURE_WARNING_CODE = (
    "fixture_non_exportable_not_governance_finding"
)

_REPORT_BODY_FIELDS = {
    "schema_version",
    "report_kind",
    "aggregate_result_fingerprint",
    "identities",
    "execution",
    "governance_use_status",
    "completeness",
    "budget_usage",
    "phase_breakdowns",
    "variation_summary",
    "claim_boundary",
    "limitation_codes",
    "warning_codes",
    "privacy",
}
_REPORT_FIELDS = _REPORT_BODY_FIELDS | {"report_fingerprint"}
_IDENTITY_FIELDS = {
    "run_id",
    "project_id",
    "input_fingerprint",
    "extension_fingerprint",
    "launch_fingerprint",
    "slot_set_fingerprint",
    "asset_bundle_id",
    "asset_bundle_fingerprint",
    "template_mapping_id",
    "template_mapping_fingerprint",
    "provider_profile_id",
    "provider_profile_fingerprint",
}
_EXECUTION_FIELDS = {
    "execution_provenance",
    "publication_status",
    "exportable",
}


class LiveReportExportError(ValueError):
    """A controlled error in report projection or rendering."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "live_report_export_invalid",
    ):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LiveReportArtifacts:
    """In-memory report artifacts; this module never writes them to disk."""

    document: dict[str, Any]
    json_bytes: bytes
    html_bytes: bytes
    json_sha256: str
    html_sha256: str


def _fail(message: str, code: str) -> None:
    raise LiveReportExportError(message, code=code)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiveReportExportError(
            "report material is not strict canonical JSON",
            code="live_report_canonical_json_invalid",
        ) from exc


def _canonical_clone(value: Any) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise LiveReportExportError(
            "report material cannot be cloned",
            code="live_report_canonical_json_invalid",
        ) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_document(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _exact_dict(
    value: Any,
    fields: set[str],
    path: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(
            f"{path} fields are invalid",
            "live_report_fields_invalid",
        )
    return value


def _warning_codes(provenance: str) -> list[str]:
    codes = list(LIVE_REPORT_COMMON_WARNING_CODES)
    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        codes.append(LIVE_REPORT_FIXTURE_WARNING_CODE)
    return codes


def _governance_use_status(provenance: str) -> str:
    if provenance == DETERMINISTIC_TRANSPORT_PROVENANCE:
        return "not_permitted_as_governance_finding"
    return "model_conditional_decision_support_only"


def _identities(result: Mapping[str, Any]) -> dict[str, str]:
    launch = result["launch_identity"]
    slot_set = result["slot_set_identity"]
    lineage = result["lineage"]
    return {
        "run_id": result["run_id"],
        "project_id": result["project_id"],
        "input_fingerprint": result["input_fingerprint"],
        "extension_fingerprint": result["extension_fingerprint"],
        "launch_fingerprint": launch["launch_fingerprint"],
        "slot_set_fingerprint": slot_set["slot_set_fingerprint"],
        "asset_bundle_id": lineage["asset_bundle_id"],
        "asset_bundle_fingerprint": lineage[
            "asset_bundle_fingerprint"
        ],
        "template_mapping_id": lineage["template_mapping_id"],
        "template_mapping_fingerprint": lineage[
            "template_mapping_fingerprint"
        ],
        "provider_profile_id": lineage["provider_profile_id"],
        "provider_profile_fingerprint": lineage[
            "provider_profile_fingerprint"
        ],
    }


def build_live_report_document(
    aggregate_result: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one validated aggregate result into report-document-v1."""

    try:
        result = validate_live_aggregate_result_v2(
            aggregate_result,
            run=run,
            extension=extension,
            launch_identity=launch_identity,
            slot_set_identity=slot_set_identity,
        )
    except LiveResultContractError as exc:
        raise LiveReportExportError(
            "aggregate result is not reportable",
            code="live_report_aggregate_result_invalid",
        ) from exc
    provenance = result["execution"]["execution_provenance"]
    if provenance not in {
        DETERMINISTIC_TRANSPORT_PROVENANCE,
        LIVE_LLM_EXECUTION_PROVENANCE,
    }:
        _fail(
            "aggregate result provenance is not reportable",
            "live_report_provenance_invalid",
        )
    body: dict[str, Any] = {
        "schema_version": LIVE_REPORT_SCHEMA_VERSION,
        "report_kind": LIVE_REPORT_KIND,
        "aggregate_result_fingerprint": result["result_fingerprint"],
        "identities": _identities(result),
        "execution": deepcopy(result["execution"]),
        "governance_use_status": _governance_use_status(provenance),
        "completeness": deepcopy(result["completeness"]),
        "budget_usage": deepcopy(result["budget_usage"]),
        "phase_breakdowns": deepcopy(result["phase_breakdowns"]),
        "variation_summary": deepcopy(result["variation_summary"]),
        "claim_boundary": deepcopy(result["claim_boundary"]),
        "limitation_codes": deepcopy(result["limitation_codes"]),
        "warning_codes": _warning_codes(provenance),
        "privacy": deepcopy(LIVE_REPORT_PRIVACY),
    }
    fingerprint = _sha256_document(body)
    return {**body, "report_fingerprint": fingerprint}


def validate_live_report_document(
    document: Mapping[str, Any],
    *,
    aggregate_result: Mapping[str, Any],
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Reproduce the report from its immutable aggregate and compare exactly."""

    cloned = _canonical_clone(document)
    report = _exact_dict(cloned, _REPORT_FIELDS, "report")
    supplied_fingerprint = report["report_fingerprint"]
    if (
        type(supplied_fingerprint) is not str
        or len(supplied_fingerprint) != 64
        or any(
            character not in "0123456789abcdef"
            for character in supplied_fingerprint
        )
    ):
        _fail(
            "report fingerprint is invalid",
            "live_report_fingerprint_invalid",
        )
    body = {
        key: report[key]
        for key in report
        if key != "report_fingerprint"
    }
    if _sha256_document(body) != supplied_fingerprint:
        _fail(
            "report fingerprint mismatch",
            "live_report_fingerprint_mismatch",
        )
    expected = build_live_report_document(
        aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    if report != expected:
        _fail(
            "report does not match its aggregate result",
            "live_report_projection_mismatch",
        )
    _exact_dict(report["identities"], _IDENTITY_FIELDS, "identities")
    _exact_dict(report["execution"], _EXECUTION_FIELDS, "execution")
    if report["claim_boundary"] != LIVE_RESULT_CLAIM_BOUNDARY:
        _fail(
            "report claim boundary was broadened",
            "live_report_claim_boundary_invalid",
        )
    if report["privacy"] != LIVE_REPORT_PRIVACY:
        _fail(
            "report privacy declaration was changed",
            "live_report_privacy_invalid",
        )
    return deepcopy(expected)


def render_live_report_json_bytes(
    document: Mapping[str, Any],
    *,
    aggregate_result: Mapping[str, Any],
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> bytes:
    """Return compact canonical UTF-8 JSON bytes."""

    validated = validate_live_report_document(
        document,
        aggregate_result=aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    return _canonical_json(validated).encode("utf-8")


def _escaped(value: Any) -> str:
    if type(value) is bool:
        text = "true" if value else "false"
    elif value is None:
        text = "n/a"
    else:
        text = str(value)
    return html.escape(text, quote=True)


def _identity_rows(identities: Mapping[str, Any]) -> str:
    labels = (
        ("Run", "run_id"),
        ("Project", "project_id"),
        ("Input SHA-256", "input_fingerprint"),
        ("Extension SHA-256", "extension_fingerprint"),
        ("Launch SHA-256", "launch_fingerprint"),
        ("Slot set SHA-256", "slot_set_fingerprint"),
        ("Asset bundle", "asset_bundle_id"),
        ("Asset SHA-256", "asset_bundle_fingerprint"),
        ("Template mapping", "template_mapping_id"),
        ("Mapping SHA-256", "template_mapping_fingerprint"),
        ("Provider profile", "provider_profile_id"),
        ("Profile SHA-256", "provider_profile_fingerprint"),
    )
    return "".join(
        "<tr><th>{}</th><td><code>{}</code></td></tr>".format(
            _escaped(label),
            _escaped(identities[key]),
        )
        for label, key in labels
    )


def _usage_rows(usage: Mapping[str, Any]) -> str:
    labels = (
        ("Completed turns", "turns_completed"),
        ("Known provider calls", "provider_calls_known"),
        ("Unknown-outcome calls", "provider_calls_unknown"),
        ("Reserved calls", "provider_calls_reserved"),
        ("Cache hits", "cache_hits"),
        ("Reported tokens", "provider_reported_tokens"),
        ("Risk-charged tokens", "risk_charged_tokens"),
        ("In-flight token reserve", "tokens_in_flight"),
        ("Effective call limit", "effective_provider_call_limit"),
        ("Effective token limit", "effective_token_limit"),
        ("Peak concurrency", "peak_concurrency"),
    )
    return "".join(
        "<tr><th>{}</th><td class=\"number\">{}</td></tr>".format(
            _escaped(label),
            _escaped(usage[key]),
        )
        for label, key in labels
    )


def _distribution_text(distributions: list[dict[str, Any]]) -> str:
    blocks = []
    for distribution in distributions:
        dimension = _escaped(distribution["dimension_id"])
        if distribution["status"] == "empty":
            blocks.append(f"<div><strong>{dimension}</strong>: empty</div>")
            continue
        cells = ", ".join(
            "{}={} ({} ppm)".format(
                _escaped(cell["category_id"]),
                _escaped(cell["count"]),
                _escaped(cell["ppm"]),
            )
            for cell in distribution["cells"]
        )
        blocks.append(
            f"<div><strong>{dimension}</strong>: {cells}</div>"
        )
    return "".join(blocks)


def _breakdown_rows(breakdowns: list[dict[str, Any]]) -> str:
    rows = []
    for row in breakdowns:
        rows.append(
            "<tr>"
            f"<td>{_escaped(row['seed'])}</td>"
            f"<td>{_escaped(row['branch_id'])}</td>"
            f"<td>{_escaped(row['policy_template_key'])}</td>"
            f"<td>{_escaped(row['phase_id'])}</td>"
            f"<td class=\"number\">{_escaped(row['completed_turns'])}</td>"
            f"<td>{_distribution_text(row['distributions'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _variation_rows(variation: Mapping[str, Any]) -> str:
    rows = []
    for entry in variation["entries"]:
        rows.append(
            "<tr>"
            f"<td>{_escaped(entry['branch_id'])}</td>"
            f"<td>{_escaped(entry['policy_template_key'])}</td>"
            f"<td>{_escaped(entry['phase_id'])}</td>"
            f"<td>{_escaped(entry['dimension_id'])}</td>"
            f"<td>{_escaped(entry['status'])}</td>"
            f"<td class=\"number\">{_escaped(entry['seed_pair_count'])}</td>"
            f"<td class=\"number\">{_escaped(entry['minimum_distance_ppm'])}</td>"
            f"<td class=\"number\">{_escaped(entry['maximum_distance_ppm'])}</td>"
            f"<td class=\"number\">{_escaped(entry['mean_distance_ppm'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _claim_rows(claims: Mapping[str, Any]) -> str:
    order = (
        "result_kind",
        "causal_status",
        "forecast_status",
        "population_status",
        "individual_status",
        "observed_status",
        "real_time_status",
    )
    return "".join(
        "<tr><th>{}</th><td><code>{}</code></td></tr>".format(
            _escaped(key),
            _escaped(claims[key]),
        )
        for key in order
    )


def _code_list(values: list[str]) -> str:
    return "".join(f"<li><code>{_escaped(value)}</code></li>" for value in values)


_REPORT_CSS = """
:root{color-scheme:light;--ink:#172033;--muted:#5b6474;--line:#d9dfeb;
--panel:#f7f9fc;--accent:#2458d3;--danger:#9f1d2d;--danger-bg:#fff1f2}
*{box-sizing:border-box}body{margin:0;background:#edf1f7;color:var(--ink);
font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1280px;margin:0 auto;padding:32px}.sheet{background:white;
border:1px solid var(--line);border-radius:16px;padding:28px;
box-shadow:0 12px 32px rgba(28,40,70,.08)}h1{font-size:28px;margin:0 0 4px}
h2{font-size:18px;margin:28px 0 10px}.sub{color:var(--muted);margin:0 0 20px}
.banner{border:2px solid var(--accent);background:#eef4ff;border-radius:10px;
padding:14px 16px;font-weight:750}.banner.fixture{border-color:var(--danger);
background:var(--danger-bg);color:var(--danger);font-size:17px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.metric{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px}.metric span{display:block;color:var(--muted);font-size:12px}
.metric strong{font-size:22px}table{width:100%;border-collapse:collapse;
font-size:12px}th,td{padding:8px;border:1px solid var(--line);text-align:left;
vertical-align:top}thead th{background:var(--panel)}code{font:11px/1.45
ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
.number{text-align:right;font-variant-numeric:tabular-nums}.scroll{overflow:auto}
ul{margin:8px 0;padding-left:20px}.foot{margin-top:28px;padding-top:14px;
border-top:1px solid var(--line);color:var(--muted);font-size:12px}
@media(max-width:800px){main{padding:12px}.sheet{padding:16px}.grid{
grid-template-columns:repeat(2,minmax(0,1fr))}}
@media print{body{background:white}main{max-width:none;padding:0}.sheet{
border:0;box-shadow:none}.scroll{overflow:visible}}
""".strip()


def render_live_report_html_bytes(
    document: Mapping[str, Any],
    *,
    aggregate_result: Mapping[str, Any],
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> bytes:
    """Render a deterministic, escaped, inactive single-file HTML report."""

    report = validate_live_report_document(
        document,
        aggregate_result=aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    fixture = (
        report["execution"]["execution_provenance"]
        == DETERMINISTIC_TRANSPORT_PROVENANCE
    )
    if fixture:
        banner_class = "banner fixture"
        banner = (
            "DETERMINISTIC FIXTURE · NON-EXPORTABLE · "
            "NOT A GOVERNANCE FINDING"
        )
    else:
        banner_class = "banner"
        banner = (
            "AUTHORIZED LIVE MODEL OUTPUT · MODEL-CONDITIONAL · "
            "NON-CAUSAL · NOT A FORECAST"
        )
    completeness = report["completeness"]
    usage = report["budget_usage"]
    privacy_rows = "".join(
        "<tr><th>{}</th><td><code>{}</code></td></tr>".format(
            _escaped(key),
            _escaped(value),
        )
        for key, value in report["privacy"].items()
    )
    markup = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>CampusPulse Aggregate Simulation Report</title>
<style>{_REPORT_CSS}</style>
</head>
<body>
<main><article class="sheet">
<h1>CampusPulse 聚合仿真报告</h1>
<p class="sub">Competition RC · auditable aggregate-result-v2 export</p>
<div class="{banner_class}">{_escaped(banner)}</div>
<h2>完整性与预算</h2>
<div class="grid">
<div class="metric"><span>Expected turns</span><strong>{_escaped(completeness['expected_turns'])}</strong></div>
<div class="metric"><span>Completed turns</span><strong>{_escaped(completeness['completed_turns'])}</strong></div>
<div class="metric"><span>Known + unknown calls</span><strong>{_escaped(usage['provider_calls_completed'])}</strong></div>
<div class="metric"><span>Cache hits</span><strong>{_escaped(usage['cache_hits'])}</strong></div>
</div>
<div class="scroll"><table><tbody>{_usage_rows(usage)}</tbody></table></div>
<h2>审计身份</h2>
<div class="scroll"><table><tbody>{_identity_rows(report['identities'])}</tbody></table></div>
<h2>Seed × Policy × Phase 聚合分布</h2>
<div class="scroll"><table>
<thead><tr><th>Seed</th><th>Branch</th><th>Policy</th><th>Phase</th><th>Turns</th><th>Controlled distributions</th></tr></thead>
<tbody>{_breakdown_rows(report['phase_breakdowns'])}</tbody>
</table></div>
<h2>跨 Seed 变异摘要（整数 ppm）</h2>
<div class="scroll"><table>
<thead><tr><th>Branch</th><th>Policy</th><th>Phase</th><th>Dimension</th><th>Status</th><th>Pairs</th><th>Min</th><th>Max</th><th>Mean</th></tr></thead>
<tbody>{_variation_rows(report['variation_summary'])}</tbody>
</table></div>
<h2>结论边界</h2>
<table><tbody>{_claim_rows(report['claim_boundary'])}</tbody></table>
<h2>限制与警示</h2>
<ul>{_code_list(report['limitation_codes'])}{_code_list(report['warning_codes'])}</ul>
<h2>隐私声明</h2>
<table><tbody>{privacy_rows}</tbody></table>
<p class="foot">Report SHA-256: <code>{_escaped(report['report_fingerprint'])}</code><br>
Aggregate result SHA-256: <code>{_escaped(report['aggregate_result_fingerprint'])}</code></p>
</article></main>
</body>
</html>"""
    encoded = markup.encode("utf-8")
    lowered = markup.lower()
    forbidden_fragments = (
        "<script",
        "<iframe",
        "<object",
        "<embed",
        "<form",
        "<img",
        "<link",
        "http://",
        "https://",
        "javascript:",
        "data:",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        _fail(
            "HTML report contains active or external content",
            "live_report_html_safety_invalid",
        )
    return encoded


def build_live_report_artifacts(
    aggregate_result: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    launch_identity: Mapping[str, Any],
    slot_set_identity: Mapping[str, Any],
) -> LiveReportArtifacts:
    """Build the document plus byte-stable JSON and HTML in memory."""

    document = build_live_report_document(
        aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    json_bytes = render_live_report_json_bytes(
        document,
        aggregate_result=aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    html_bytes = render_live_report_html_bytes(
        document,
        aggregate_result=aggregate_result,
        run=run,
        extension=extension,
        launch_identity=launch_identity,
        slot_set_identity=slot_set_identity,
    )
    return LiveReportArtifacts(
        document=document,
        json_bytes=json_bytes,
        html_bytes=html_bytes,
        json_sha256=_sha256_bytes(json_bytes),
        html_sha256=_sha256_bytes(html_bytes),
    )
