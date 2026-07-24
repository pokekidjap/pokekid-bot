from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse(relative_path: str) -> ast.Module:
    return ast.parse(
        (ROOT / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )


def callback_patterns() -> list[str]:
    patterns = []
    for node in ast.walk(parse("main.py")):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "CallbackQueryHandler"
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "pattern"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                patterns.append(keyword.value.value)
    return patterns


class ShippingV2CallbackAuditTests(unittest.TestCase):
    def test_v2_static_callbacks_are_reachable(self):
        patterns = [re.compile(pattern) for pattern in callback_patterns()]
        callbacks = {
            "shipping_v2_continue",
            "shipping_v2_page:2",
            "shipping_v2_carrier:0",
            "shipping_v2_resume",
            "shipping_v2_cancel",
            "shipping_v2_cancel_draft",
            "shipping_v2_change_items",
            "shipping_payment",
            "shipping_receipt_cancel",
            "shipping_v2_join",
            "join_v2_page:2",
            "join_v2_refresh",
            "join_v2_confirm",
            "join_v2_cancel",
            "admin_shipping_cancel:SP-1",
            "admin_shipping_cancel_confirm:SP-1",
            "admin_shipping_cancel_back:SP-1",
        }
        for callback in callbacks:
            with self.subTest(callback=callback):
                self.assertTrue(
                    any(pattern.fullmatch(callback) for pattern in patterns)
                )

    def test_v2_item_callback_pattern_is_reachable(self):
        callbacks = (
            "order_v2_toggle:"
            "ART-ABCDEF00-0000-4000-8000-000000000001",
            "join_v2_toggle:"
            "ART-ABCDEF00-0000-4000-8000-000000000001",
        )
        for callback in callbacks:
            with self.subTest(callback=callback):
                self.assertTrue(
                    any(
                        re.fullmatch(pattern, callback)
                        for pattern in callback_patterns()
                    )
                )

    def test_specific_handlers_precede_generic_router(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        generic = (
            "application.add_handler(CallbackQueryHandler(handle_button))"
        )
        generic_position = source.index(generic)
        for handler in (
            "toggle_v2_available_item",
            "change_v2_items_page",
            "continue_v2_shipping",
            "select_v2_shipping_carrier",
            "resume_v2_shipping",
            "cancel_v2_shipping",
            "toggle_shipping_v2_join_item",
            "change_shipping_v2_join_page",
            "refresh_shipping_v2_join",
            "confirm_shipping_v2_join",
            "show_admin_shipping_cancel_confirmation",
            "confirm_admin_shipping_cancel",
            "return_admin_shipping_detail",
        ):
            self.assertLess(source.index(handler, source.index("def register_handlers")), generic_position)

    def test_no_duplicate_v2_patterns(self):
        patterns = [
            pattern
            for pattern in callback_patterns()
            if "v2" in pattern
        ]
        self.assertEqual(len(patterns), len(set(patterns)))

    def test_all_static_callback_literals_fit_telegram_limit(self):
        for path in (ROOT / "keyboards").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword):
                    continue
                if node.arg != "callback_data":
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value,
                    str,
                ):
                    self.assertLessEqual(
                        len(node.value.value.encode("utf-8")),
                        64,
                        f"{path.name}: {node.value.value}",
                    )

    def test_v2_callback_handlers_confirm_query_once_by_design(self):
        tree = parse("modules/shipping_v2.py")
        expected = {
            "show_v2_available_orders",
            "toggle_v2_available_item",
            "change_v2_items_page",
            "continue_v2_shipping",
            "resume_v2_shipping",
            "select_v2_shipping_carrier",
            "cancel_v2_shipping",
            "start_v2_shipping_payment",
            "cancel_v2_shipping_receipt",
        }
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in expected:
            calls = [
                node
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "answer"
                    )
                    or (
                        isinstance(node.func, ast.Name)
                        and node.func.id
                        in {"_require_v2", "_answer_query"}
                    )
                )
            ]
            # _require_v2 risponde solo nel ramo feature-off; il ramo attivo
            # contiene una sola conferma, diretta o tramite _answer_query.
            active_answers = [
                node
                for node in calls
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "answer"
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "_answer_query"
                )
            ]
            if name == "cancel_v2_shipping_receipt":
                self.assertEqual(len(active_answers), 1)
            else:
                self.assertEqual(
                    len(active_answers),
                    1,
                    name,
                )
                self.assertTrue(
                    any(
                        isinstance(node.func, ast.Name)
                        and node.func.id == "_require_v2"
                        for node in calls
                    ),
                    name,
                )

    def test_receipt_conversation_has_v2_cancel_command(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("def build_shipping_conversation_handler")
        end = source.index("def build_admin_tracking_handler")
        block = source[start:end]
        self.assertIn(
            'CommandHandler("cancel", cancel_shipping_receipt_command)',
            block,
        )

    def test_join_conversation_has_username_state_and_cancel_command(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index(
            "def build_shipping_v2_join_conversation_handler"
        )
        end = source.index("def build_admin_tracking_handler")
        block = source[start:end]
        self.assertIn("SHIPPING_V2_JOIN_USERNAME", block)
        self.assertIn(
            'CommandHandler("cancel", cancel_shipping_v2_join)',
            block,
        )
        self.assertEqual(block.count('pattern=r"^join_v2_cancel$"'), 1)

    def test_join_callbacks_are_answered_on_all_simulated_paths(self):
        tree = parse("modules/shipping_v2_join.py")
        expected = {
            "start_shipping_v2_join",
            "toggle_shipping_v2_join_item",
            "change_shipping_v2_join_page",
            "refresh_shipping_v2_join",
            "confirm_shipping_v2_join",
            "cancel_shipping_v2_join",
        }
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in expected:
            answers = [
                node
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "answer"
            ]
            self.assertGreaterEqual(len(answers), 1, name)


if __name__ == "__main__":
    unittest.main()
