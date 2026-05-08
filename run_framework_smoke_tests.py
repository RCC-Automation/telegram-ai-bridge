from __future__ import annotations

import json
from pathlib import Path

from appstudio_gen import build_project_from_spec, load_spec


ROOT = Path(r"C:\Users\barru\Documents\New project\telegram-ai-bridge")
APPSTUDIO_ROOT = ROOT / "artifacts" / "AppStudio"
DEFAULT_ASSET_SOURCE = APPSTUDIO_ROOT / "WebappInBuildFunct" / "Assets" / "images"

SPEC_PATHS = [
    ROOT / "project_specs" / "webapp_canonical_operator_app.json",
    ROOT / "project_specs" / "webapp_canonical_typed_data_app.json",
    ROOT / "project_specs" / "webapp_canonical_service_facade_app.json",
    ROOT / "project_specs" / "webapp_abb_typed_shapes_demo.json",
    ROOT / "project_specs" / "webapp_framework_showcase.json",
]


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    results: list[dict[str, object]] = []
    for spec_path in SPEC_PATHS:
        spec = load_spec(spec_path)
        project_dir, package_path = build_project_from_spec(
            spec,
            workspace_root=ROOT,
            default_asset_source=DEFAULT_ASSET_SOURCE,
            appstudio_root=APPSTUDIO_ROOT,
        )
        validation_report_path = project_dir / "framework_validation_report.md"
        custom_function_report_path = project_dir / "custom_function_report.md"
        fit_report_path = project_dir / "layout_fit_report.json"
        fit_items = _load_json(fit_report_path) if fit_report_path.exists() else []
        results.append(
            {
                "spec": str(spec_path),
                "project_dir": str(project_dir),
                "package_path": str(package_path),
                "validation_report": _load_text(validation_report_path) if validation_report_path.exists() else "",
                "custom_function_report": _load_text(custom_function_report_path) if custom_function_report_path.exists() else "",
                "fit_issue_count": len(fit_items) if isinstance(fit_items, list) else None,
                "fit_issues": fit_items,
            }
        )

    out_json = ROOT / "artifacts" / "framework_smoke_test_report.json"
    out_md = ROOT / "artifacts" / "framework_smoke_test_report.md"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = ["Framework smoke test report", ""]
    for item in results:
        lines.append(f"- {Path(str(item['spec'])).name}")
        lines.append(f"  - Project: {item['project_dir']}")
        lines.append(f"  - Package: {item['package_path']}")
        lines.append(f"  - Validation: {item['validation_report'] or 'n/a'}")
        custom_function_report = str(item.get("custom_function_report") or "").splitlines()
        custom_function_summary = next((line for line in custom_function_report if line.startswith("- Missing symbols:")), "n/a")
        if custom_function_summary.startswith("- "):
            custom_function_summary = custom_function_summary[2:]
        lines.append(f"  - Custom functions: {custom_function_summary}")
        fit_issue_count = item["fit_issue_count"]
        if fit_issue_count in (0, None):
            lines.append("  - Layout fit: no issues reported")
        else:
            lines.append(f"  - Layout fit: {fit_issue_count} issue(s) reported")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote JSON report: {out_json}")
    print(f"Wrote Markdown report: {out_md}")


if __name__ == "__main__":
    main()
