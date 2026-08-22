"""Verify the optional Lovelace dashboard package."""

import importlib.util
from pathlib import Path
import tempfile

from verification.support import VerifyCase


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_MODULE_PATH = ROOT / "custom_components" / "fn_nas" / "dashboard.py"
FRONTEND_MODULE_PATH = ROOT / "custom_components" / "fn_nas" / "frontend.py"
DASHBOARD_VIEW_PATH = ROOT / "dashboard" / "fn_nas_view.yaml"
DASHBOARD_TEMPLATE_PATH = ROOT / "dashboard" / "button_card_templates.yaml"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DashboardVerifications(VerifyCase):
    def verify_dashboard_metadata_is_stable_and_namespaced(self):
        module = _load_module("fn_nas_dashboard", DASHBOARD_MODULE_PATH)

        metadata = module.dashboard_metadata("fan", "rpm", order=20)

        self.assertEqual(
            metadata,
            {
                "fn_nas_dashboard_category": "fan",
                "fn_nas_dashboard_role": "rpm",
                "fn_nas_dashboard_order": 20,
            },
        )

    def verify_frontend_installer_copies_only_declared_assets(self):
        module = _load_module("fn_nas_frontend", FRONTEND_MODULE_PATH)

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            for filename in module.DASHBOARD_ASSET_FILES:
                (source / filename).write_bytes(f"asset:{filename}".encode("ascii"))
            (source / "not-declared.png").write_bytes(b"ignore")

            copied = module.copy_dashboard_assets(source, target)

            self.assertEqual(sorted(copied), sorted(module.DASHBOARD_ASSET_FILES))
            self.assertFalse((target / "not-declared.png").exists())
            for filename in module.DASHBOARD_ASSET_FILES:
                self.assertEqual(
                    (target / filename).read_bytes(),
                    f"asset:{filename}".encode("ascii"),
                )

    def verify_frontend_installer_updates_changed_same_size_asset(self):
        module = _load_module("fn_nas_frontend_update", FRONTEND_MODULE_PATH)

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            filename = module.DASHBOARD_ASSET_FILES[0]
            (source / filename).write_bytes(b"new")
            (target / filename).write_bytes(b"old")

            copied = module.copy_dashboard_assets(source, target)

            self.assertEqual(copied, [filename])
            self.assertEqual((target / filename).read_bytes(), b"new")

    async def verify_async_installer_uses_home_assistant_paths(self):
        module = _load_module("fn_nas_frontend_async", FRONTEND_MODULE_PATH)

        class Config:
            def __init__(self, root):
                self.root = Path(root)

            def path(self, *parts):
                return str(self.root.joinpath(*parts))

        class Hass:
            def __init__(self, root):
                self.config = Config(root)

            async def async_add_executor_job(self, function, *args):
                return function(*args)

        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source = root / "custom_components" / "fn_nas" / "frontend"
            source.mkdir(parents=True)
            for filename in module.DASHBOARD_ASSET_FILES:
                (source / filename).write_bytes(filename.encode("ascii"))

            copied = await module.async_install_dashboard_assets(Hass(root))

            self.assertEqual(sorted(copied), sorted(module.DASHBOARD_ASSET_FILES))
            self.assertTrue(
                (root / "www" / "community" / "fn_nas" / module.DASHBOARD_ASSET_FILES[0]).is_file()
            )

    def verify_dashboard_yaml_has_required_dynamic_sections(self):
        view = DASHBOARD_VIEW_PATH.read_text(encoding="utf-8")
        templates = DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")

        self.assertIn("title: 飞牛NAS", view)
        self.assertIn("/local/community/fn_nas/fn_nas.png", view)
        self.assertIn("custom:auto-entities", view)
        self.assertIn("custom:mini-graph-card", view)
        self.assertEqual(view.count("card_param: entities"), 2)
        self.assertIn('background: "#11181c"', view)
        self.assertIn("background: transparent", view)
        self.assertIn("- border: none", templates)
        self.assertIn("custom:button-card", view)
        self.assertIn("card_mod:", view)
        for category in ("system", "storage", "disk", "fan", "ups", "vm", "docker", "control"):
            self.assertIn(f"fn_nas_dashboard_category: {category}", view)
        self.assertIn("fn_nas_metric", templates)

    def verify_dashboard_uses_graphical_storage_disk_and_fan_views(self):
        view = DASHBOARD_VIEW_PATH.read_text(encoding="utf-8")
        templates = DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")

        for template in ("fn_nas_volume", "fn_nas_disk", "fn_nas_fan"):
            self.assertIn(template, templates)
            self.assertIn(f"template: {template}", view)

        self.assertIn("fn_nas_dashboard_role: temperature", view)
        self.assertIn("fn_nas_dashboard_role: rpm", view)
        self.assertIn("max_columns: 2", view)
        self.assertIn("type: fan-speed", view)
        self.assertIn("type: select-options", view)
        self.assertIn("conic-gradient", templates)
        self.assertIn("@keyframes fn-nas-spin", templates)
        self.assertIn("使用率", templates)
        self.assertIn("PWM百分比", templates)

    def verify_readme_documents_manual_dashboard_installation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## 仪表盘", readme)
        self.assertIn("button-card", readme)
        self.assertIn("auto-entities", readme)
        self.assertIn("card-mod", readme)
        self.assertIn("mini-graph-card", readme)
        self.assertIn("dashboard/fn_nas_view.yaml", readme)
        self.assertIn("HACS 不会自动修改", readme)
