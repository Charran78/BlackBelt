import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from blackbelt.core import gatekeeper
from blackbelt.tools import suggest


class RecipeCatalogTests(unittest.TestCase):
    def test_catalog_has_no_duplicate_yaml_keys(self) -> None:
        source = suggest.RECIPES_PATH.read_text(encoding="utf-8")
        document = yaml.compose(source)
        self.assertIsNotNone(document)

        def check_node(node: object) -> None:
            if isinstance(node, MappingNode):
                keys = [
                    key.value
                    for key, _ in node.value
                    if isinstance(key, ScalarNode)
                ]
                self.assertEqual(len(keys), len(set(keys)))
                for key, value in node.value:
                    check_node(key)
                    check_node(value)
            elif isinstance(node, SequenceNode):
                for child in node.value:
                    check_node(child)

        check_node(document)

    def test_loads_forty_one_unique_recipes_for_supported_platforms(self) -> None:
        recipes = suggest._load_recipes()
        expected_additional_ids = {
            "list_directory",
            "find_directories",
            "find_empty_files",
            "recent_files",
            "count_files",
            "read_file_head",
            "git_status",
            "git_branch",
            "git_log",
            "git_changed_files",
            "git_diff_summary",
            "network_check",
            "dns_lookup",
            "ip_config",
            "system_info",
            "uptime",
            "process_by_name",
            "which_command",
            "file_size",
            "copy_file",
            "create_directory",
            "git_staged_files",
            "activate_virtualenv",
            "run_uvicorn",
            "run_streamlit",
            "run_gradio",
            "docker_compose_up",
            "docker_compose_down",
            "run_django",
            "run_flask",
            "run_rust",
            "test_rust",
            "run_vite",
        }
        recipe_ids = {recipe["id"] for recipe in recipes}

        self.assertEqual(len(recipes), 41)
        self.assertEqual(len(recipe_ids), len(recipes))
        self.assertLessEqual(expected_additional_ids, recipe_ids)
        for recipe in recipes:
            for os_name in recipe["os"]:
                with self.subTest(recipe=recipe["id"], os=os_name):
                    self.assertIn(os_name, recipe["comandos"])

    def test_every_recipe_has_complete_commands_questions_and_risks(self) -> None:
        expected_os = {"linux", "macos", "windows"}
        valid_risks = {"SAFE", "WARN", "CRITICAL"}

        for recipe in suggest._load_recipes():
            with self.subTest(recipe=recipe["id"]):
                question_ids = [question["id"] for question in recipe["preguntas"]]
                self.assertEqual(len(question_ids), len(set(question_ids)))
                self.assertTrue(set(recipe["os"]) <= expected_os)

                for os_name in recipe["os"]:
                    commands = recipe["comandos"][os_name]
                    command_keys = [
                        key
                        for key in ("cmd", "cmd_preview", "cmd_borrar")
                        if key in commands
                    ]
                    self.assertTrue(command_keys)

                    for command_key in command_keys:
                        template = commands[command_key]
                        self.assertIsInstance(template, str)
                        self.assertTrue(template.strip())
                        placeholders = set(
                            re.findall(r"{([a-z][a-z0-9_]*)}", template)
                        )
                        self.assertLessEqual(placeholders, set(question_ids))

                    risk_keys = {
                        "cmd": "riesgo",
                        "cmd_preview": "riesgo_preview",
                        "cmd_borrar": "riesgo_borrar",
                    }
                    for command_key in command_keys:
                        risk = commands.get(risk_keys[command_key])
                        self.assertIn(risk, valid_risks)
                        self.assertTrue(commands.get("descripcion"))

                for question in recipe["preguntas"]:
                    self.assertIn(question["tipo"], {"opcion", "path", "texto"})
                    if question["tipo"] == "opcion":
                        self.assertIn(question["default"], question["opciones"])
                        for values_by_os in question.get("valores", {}).values():
                            self.assertTrue(
                                set(values_by_os) <= set(question["opciones"])
                            )

    def test_git_diff_options_render_nonempty_safe_arguments(self) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "git_diff_summary"
        )

        for selection, expected in (("1", "--cached"), ("2", "--")):
            with self.subTest(selection=selection):
                with patch(
                    "blackbelt.tools.suggest.Prompt.ask",
                    return_value=selection,
                ):
                    answers = suggest._ask_questions(recipe, "windows")

                self.assertIsNotNone(answers)
                command_values, _ = answers
                self.assertEqual(command_values["area"], expected)
                self.assertEqual(
                    suggest._render(
                        recipe["comandos"]["windows"]["cmd"],
                        command_values,
                    ),
                    f"git diff {expected}",
                )

    def test_virtualenv_recipe_renders_for_each_shell(self) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "activate_virtualenv"
        )
        expected_commands = {
            "linux": "source .venv/bin/activate",
            "macos": "source .venv/bin/activate",
            "windows": ". (Join-Path '.venv' 'Scripts/Activate.ps1')",
        }

        for os_name, expected in expected_commands.items():
            with self.subTest(os=os_name):
                with patch(
                    "blackbelt.tools.suggest.Prompt.ask",
                    return_value=".venv",
                ):
                    answers = suggest._ask_questions(recipe, os_name)

                self.assertIsNotNone(answers)
                command_values, _ = answers
                self.assertEqual(
                    suggest._render(
                        recipe["comandos"][os_name]["cmd"],
                        command_values,
                    ),
                    expected,
                )

    def test_development_server_and_docker_commands_render_per_shell(self) -> None:
        recipes = {
            recipe["id"]: recipe
            for recipe in suggest._load_recipes()
        }
        expected_commands = {
            "run_uvicorn": {
                "linux": "python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000",
                "macos": "python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000",
                "windows": "python -m uvicorn 'main:app' --reload --host 127.0.0.1 --port 8000",
            },
            "run_streamlit": {
                "linux": "python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501",
                "macos": "python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501",
                "windows": "python -m streamlit run 'app.py' --server.address 127.0.0.1 --server.port 8501",
            },
            "run_gradio": {
                "linux": "GRADIO_SERVER_NAME=127.0.0.1 python app.py",
                "macos": "GRADIO_SERVER_NAME=127.0.0.1 python app.py",
                "windows": "$env:GRADIO_SERVER_NAME='127.0.0.1'; python 'app.py'",
            },
            "docker_compose_up": {
                os_name: "docker compose up --build"
                for os_name in ("linux", "macos", "windows")
            },
            "docker_compose_down": {
                os_name: "docker compose down"
                for os_name in ("linux", "macos", "windows")
            },
            "run_django": {
                "linux": "python manage.py runserver 127.0.0.1:8000",
                "macos": "python manage.py runserver 127.0.0.1:8000",
                "windows": "python 'manage.py' runserver 127.0.0.1:8000",
            },
            "run_flask": {
                "linux": "python -m flask --app app run --host 127.0.0.1 --port 5000",
                "macos": "python -m flask --app app run --host 127.0.0.1 --port 5000",
                "windows": "python -m flask --app 'app' run --host 127.0.0.1 --port 5000",
            },
            "run_rust": {
                "linux": "cargo run --manifest-path Cargo.toml",
                "macos": "cargo run --manifest-path Cargo.toml",
                "windows": "cargo run --manifest-path 'Cargo.toml'",
            },
            "test_rust": {
                "linux": "cargo test --manifest-path Cargo.toml",
                "macos": "cargo test --manifest-path Cargo.toml",
                "windows": "cargo test --manifest-path 'Cargo.toml'",
            },
            "run_vite": {
                os_name: "npm run dev -- --host 127.0.0.1"
                for os_name in ("linux", "macos", "windows")
            },
        }
        prompt_answers = {
            "run_uvicorn": ["main:app", "1"],
            "run_streamlit": ["app.py", "1"],
            "run_gradio": ["app.py"],
            "docker_compose_up": [],
            "docker_compose_down": [],
            "run_django": ["manage.py", "1"],
            "run_flask": ["app", "1"],
            "run_rust": ["Cargo.toml"],
            "test_rust": ["Cargo.toml"],
            "run_vite": [],
        }

        for recipe_id, commands_by_os in expected_commands.items():
            recipe = recipes[recipe_id]
            self.assertEqual(recipe.get("ejecucion"), "foreground")
            for os_name, expected in commands_by_os.items():
                with self.subTest(recipe=recipe_id, os=os_name):
                    with patch(
                        "blackbelt.tools.suggest.Prompt.ask",
                        side_effect=prompt_answers[recipe_id],
                    ):
                        answers = suggest._ask_questions(recipe, os_name)

                    self.assertIsNotNone(answers)
                    command_values, _ = answers
                    self.assertEqual(
                        suggest._render(
                            recipe["comandos"][os_name]["cmd"],
                            command_values,
                        ),
                        expected,
                    )

        fastapi_matches = suggest._search(
            list(recipes.values()),
            "FastAPI",
        )
        self.assertEqual(
            [recipe["id"] for recipe in fastapi_matches],
            ["run_uvicorn"],
        )

    def test_search_normalizes_accents_and_plural(self) -> None:
        recipes = suggest._load_recipes()

        matches = suggest._search(recipes, "encontrar archivos grandes")

        self.assertEqual(matches[0]["id"], "find_large_files")
        self.assertEqual(len(matches), 1)

    def test_search_keeps_multiple_recipes_when_scores_tie(self) -> None:
        recipes = suggest._load_recipes()

        matches = suggest._search(recipes, "archivos")

        self.assertGreater(len(matches), 1)
        self.assertEqual(
            len(matches),
            len({recipe["id"] for recipe in matches}),
        )

    def test_unknown_operating_system_is_not_treated_as_linux(self) -> None:
        with patch("blackbelt.tools.suggest.platform.system", return_value="FreeBSD"):
            self.assertIsNone(suggest._current_os())

    def test_quotes_untrusted_values_for_native_shell(self) -> None:
        self.assertEqual(
            suggest._quote_argument("C:\\Users\\O'Brien; Remove-Item *", "windows"),
            "'C:\\Users\\O''Brien; Remove-Item *'",
        )
        self.assertEqual(
            suggest._quote_argument("O'Brien; rm -rf /", "linux"),
            "'O'\"'\"'Brien; rm -rf /'",
        )

    def test_normalizes_windows_drive_designator_to_drive_root(self) -> None:
        self.assertEqual(suggest._normalize_windows_path("h:"), "H:\\")
        self.assertEqual(suggest._normalize_windows_path("h:/"), "H:\\")
        self.assertEqual(
            suggest._normalize_windows_path(r"h:\users\xpite"),
            r"h:\users\xpite",
        )

    def test_template_rendering_preserves_shell_script_blocks(self) -> None:
        template = "Where-Object { $_.Length -gt {size} }"

        self.assertEqual(
            suggest._render(template, {"size": "104857600"}),
            "Where-Object { $_.Length -gt 104857600 }",
        )

    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="1")
    @patch("blackbelt.tools.suggest.console.print")
    def test_rejects_unsafe_option_values_from_catalog(
        self,
        _print: Mock,
        _prompt: Mock,
    ) -> None:
        recipe = {
            "preguntas": [
                {
                    "id": "sort_by",
                    "texto": "Orden",
                    "tipo": "opcion",
                    "opciones": ["safe"],
                    "default": "safe",
                    "valores": {
                        "windows": {"safe": "safe; Remove-Item *"},
                    },
                }
            ]
        }

        self.assertIsNone(suggest._ask_questions(recipe, "windows"))

    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="")
    def test_rejects_empty_required_path(
        self,
        _prompt: Mock,
        _print: Mock,
    ) -> None:
        question = {
            "id": "archivo",
            "texto": "¿Qué archivo quieres leer?",
            "tipo": "path",
            "default": "",
        }

        self.assertIsNone(suggest._ask_question(question, "windows"))

    def test_project_discovery_is_bounded_and_skips_generated_directories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "manage.py").touch()
            (root / "service").mkdir()
            (root / "service" / "manage.py").touch()
            (root / "service" / "deep").mkdir()
            (root / "service" / "deep" / "manage.py").touch()
            (root / "service" / "deep" / "too_deep").mkdir()
            (root / "service" / "deep" / "too_deep" / "manage.py").touch()
            (root / "node_modules" / "nested").mkdir(parents=True)
            (root / "node_modules" / "nested" / "manage.py").touch()
            (root / ".git" / "nested").mkdir(parents=True)
            (root / ".git" / "nested" / "manage.py").touch()

            matches = suggest._discover_project_paths(
                {
                    "filenames": ["manage.py"],
                    "kind": "file",
                    "max_depth": 2,
                },
                root=root,
            )

        self.assertEqual(
            matches,
            ["manage.py", os.path.join("service", "deep", "manage.py"),
             os.path.join("service", "manage.py")],
        )

    def test_project_discovery_finds_hidden_virtualenv_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".venv").mkdir()
            (root / ".venv" / "Scripts").mkdir()

            matches = suggest._discover_project_paths(
                {
                    "filenames": [".venv", "venv", "env"],
                    "kind": "directory",
                },
                root=root,
            )

        self.assertEqual(matches, [".venv"])

    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="")
    @patch(
        "blackbelt.tools.suggest._discover_project_paths",
        return_value=["backend/manage.py"],
    )
    def test_blank_accepts_one_detected_path(
        self,
        _discover: Mock,
        _prompt: Mock,
        _print: Mock,
    ) -> None:
        question = {
            "id": "manage",
            "texto": "¿Dónde está manage.py?",
            "tipo": "path",
            "default": "",
            "autodetect": {"filenames": ["manage.py"]},
        }

        self.assertEqual(
            suggest._ask_question(question, "linux"),
            ("backend/manage.py", "backend/manage.py"),
        )

    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="2")
    @patch(
        "blackbelt.tools.suggest._discover_project_paths",
        return_value=["api/manage.py", "backend/manage.py"],
    )
    def test_multiple_detected_paths_require_explicit_selection(
        self,
        _discover: Mock,
        _prompt: Mock,
        _print: Mock,
    ) -> None:
        question = {
            "id": "manage",
            "texto": "¿Dónde está manage.py?",
            "tipo": "path",
            "default": "",
            "autodetect": {"filenames": ["manage.py"]},
        }

        self.assertEqual(
            suggest._ask_question(question, "linux"),
            ("backend/manage.py", "backend/manage.py"),
        )

    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="")
    @patch(
        "blackbelt.tools.suggest._discover_project_paths",
        return_value=[],
    )
    def test_blank_is_rejected_when_no_project_file_is_detected(
        self,
        _discover: Mock,
        _prompt: Mock,
        _print: Mock,
    ) -> None:
        question = {
            "id": "manage",
            "texto": "¿Dónde está manage.py?",
            "tipo": "path",
            "default": "",
            "autodetect": {"filenames": ["manage.py"]},
        }

        self.assertIsNone(suggest._ask_question(question, "linux"))

    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="")
    @patch(
        "blackbelt.tools.suggest._discover_project_paths",
        return_value=["backend/main.py"],
    )
    def test_text_autodetection_builds_module_suggestion(
        self,
        _discover: Mock,
        _prompt: Mock,
        _print: Mock,
    ) -> None:
        question = {
            "id": "aplicacion",
            "texto": "Módulo ASGI",
            "tipo": "texto",
            "default": "",
            "autodetect": {
                "filenames": ["main.py"],
                "suggestion_template": "{module}:app",
            },
        }

        self.assertEqual(
            suggest._ask_question(question, "linux"),
            ("backend.main:app", "backend.main:app"),
        )


class SuggestFlowTests(unittest.TestCase):
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=False)
    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="h:")
    def test_requires_extra_confirmation_for_entire_drive_search(
        self,
        _prompt: Mock,
        _print: Mock,
        confirm: Mock,
    ) -> None:
        question = {
            "id": "dir",
            "texto": "¿En qué carpeta buscar?",
            "tipo": "path",
            "default": ".",
        }

        answer = suggest._ask_question(question, "windows")

        self.assertIsNone(answer)
        confirm.assert_called_once()

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=False)
    @patch("blackbelt.tools.suggest.Prompt.ask")
    @patch("blackbelt.tools.suggest.console.print")
    @patch("blackbelt.tools.suggest.platform.system", return_value="Windows")
    def test_escaped_user_values_are_only_previewed(
        self,
        _system: Mock,
        _print: Mock,
        prompt: Mock,
        _confirm: Mock,
        run_command: Mock,
    ) -> None:
        prompt.side_effect = [r"C:\Temp\O'Brien", "x'; Remove-Item *"]

        suggest.run(["buscar archivos por nombre pdf"])

        run_command.assert_not_called()
        self.assertEqual(prompt.call_count, 2)

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=False)
    @patch("blackbelt.tools.suggest.Prompt.ask", return_value="2")
    @patch("blackbelt.tools.suggest.console.print")
    def test_delete_recipe_requires_confirmation_and_is_critical(
        self,
        _print: Mock,
        _prompt: Mock,
        _confirm: Mock,
        run_command: Mock,
    ) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "clean_temp"
        )
        delete_command = recipe["comandos"]["windows"]["cmd_borrar"]

        suggest._build_and_show(recipe, "windows")

        self.assertIs(
            gatekeeper.analyze_command(
                delete_command,
                gatekeeper.Shell.POWERSHELL,
            ),
            gatekeeper.Risk.CRITICAL,
        )
        run_command.assert_not_called()

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=True)
    @patch("blackbelt.tools.suggest.gatekeeper.analyze_command")
    @patch("blackbelt.tools.suggest.console.print")
    def test_accepted_suggestion_is_delegated_to_gatekeeper(
        self,
        _print: Mock,
        analyze_command: Mock,
        _confirm: Mock,
        run_command: Mock,
    ) -> None:
        analyze_command.return_value = gatekeeper.Risk.SAFE
        run_command.return_value = (0, "ok\n", "")
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "disk_usage"
        )

        suggest._build_and_show(recipe, "windows")

        run_command.assert_called_once_with(
            recipe["comandos"]["windows"]["cmd"],
            capture=True,
            minimum_risk=gatekeeper.Risk.SAFE,
        )

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask")
    @patch("blackbelt.tools.suggest.console.print")
    def test_virtualenv_activation_stays_manual(
        self,
        _console_print: Mock,
        confirm: Mock,
        run_command: Mock,
    ) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "activate_virtualenv"
        )

        with patch(
            "blackbelt.tools.suggest.Prompt.ask",
            return_value=".venv",
        ):
            suggest._build_and_show(recipe, "windows")

        confirm.assert_not_called()
        run_command.assert_not_called()

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=True)
    @patch("blackbelt.tools.suggest.console.print")
    def test_foreground_recipes_require_confirmation_and_stream_output(
        self,
        _console_print: Mock,
        confirm: Mock,
        run_command: Mock,
    ) -> None:
        prompt_answers = {
            "run_uvicorn": ["main:app", "1"],
            "run_streamlit": ["app.py", "1"],
            "run_gradio": ["app.py"],
            "docker_compose_up": [],
            "docker_compose_down": [],
            "run_django": ["manage.py", "1"],
            "run_flask": ["app", "1"],
            "run_rust": ["Cargo.toml"],
            "test_rust": ["Cargo.toml"],
            "run_vite": [],
        }
        recipes = {
            recipe["id"]: recipe
            for recipe in suggest._load_recipes()
            if recipe.get("ejecucion") == "foreground"
        }

        run_command.return_value = (0, "", "")
        self.assertEqual(set(recipes), set(prompt_answers))
        for recipe_id, answers in prompt_answers.items():
            with self.subTest(recipe=recipe_id):
                with patch(
                    "blackbelt.tools.suggest.Prompt.ask",
                    side_effect=answers,
                ):
                    suggest._build_and_show(recipes[recipe_id], "windows")

        self.assertEqual(confirm.call_count, len(prompt_answers))
        self.assertEqual(run_command.call_count, len(prompt_answers))
        for call in run_command.call_args_list:
            self.assertFalse(call.kwargs["capture"])
            self.assertEqual(
                call.kwargs["minimum_risk"],
                gatekeeper.Risk.WARN,
            )

    @patch("blackbelt.tools.suggest.gatekeeper.run_command")
    @patch("blackbelt.tools.suggest.Confirm.ask", return_value=False)
    @patch("blackbelt.tools.suggest.console.print")
    @patch(
        "blackbelt.tools.suggest.Prompt.ask",
        side_effect=["main:app", "1"],
    )
    def test_foreground_command_is_not_run_when_confirmation_is_denied(
        self,
        _prompt: Mock,
        _console_print: Mock,
        confirm: Mock,
        run_command: Mock,
    ) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "run_uvicorn"
        )

        suggest._build_and_show(recipe, "windows")

        confirm.assert_called_once()
        run_command.assert_not_called()


@unittest.skipUnless(
    os.name == "nt"
    and any(
        shutil.which(executable)
        for executable in ("powershell.exe", "powershell", "pwsh.exe", "pwsh")
    ),
    "Requiere PowerShell en Windows.",
)
class PowerShellRecipeIntegrationTests(unittest.TestCase):
    def run_recipe_command(
        self,
        recipe_id: str,
        values: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == recipe_id
        )
        command = suggest._render(
            recipe["comandos"]["windows"]["cmd"],
            values,
        )
        return subprocess.run(
            gatekeeper._shell_command(
                command,
                gatekeeper.Shell.POWERSHELL,
            ),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )

    def test_file_recipes_run_against_temporary_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text(
                "first line\nsecond line\n",
                encoding="utf-8",
            )
            (root / "empty.txt").touch()
            (root / "nested").mkdir()
            (root / "nested" / "data.txt").write_text(
                "nested data\n",
                encoding="utf-8",
            )
            quoted_root = suggest._quote_argument(
                directory,
                "windows",
                expand_user=True,
            )

            cases = (
                (
                    "list_directory",
                    {"dir": quoted_root},
                    "notes.txt",
                ),
                (
                    "find_directories",
                    {"dir": quoted_root, "patron": "'nested'"},
                    "nested",
                ),
                (
                    "find_empty_files",
                    {"dir": quoted_root},
                    "empty.txt",
                ),
                (
                    "count_files",
                    {"dir": quoted_root},
                    "3",
                ),
                (
                    "read_file_head",
                    {
                        "archivo": suggest._quote_argument(
                            str(root / "notes.txt"),
                            "windows",
                            expand_user=True,
                        ),
                        "lineas": "1",
                    },
                    "first line",
                ),
                (
                    "file_size",
                    {
                        "archivo": suggest._quote_argument(
                            str(root / "notes.txt"),
                            "windows",
                            expand_user=True,
                        ),
                    },
                    "notes.txt",
                ),
            )

            for recipe_id, values, expected_output in cases:
                with self.subTest(recipe=recipe_id):
                    result = self.run_recipe_command(recipe_id, values)
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"stdout={result.stdout}\nstderr={result.stderr}",
                    )
                    self.assertIn(expected_output, result.stdout)

    def test_copy_and_create_directory_recipes_use_temporary_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            destination = root / "copy.txt"
            new_directory = root / "created"
            source.write_text("copy fixture", encoding="utf-8")

            copy_result = self.run_recipe_command(
                "copy_file",
                {
                    "origen": suggest._quote_argument(
                        str(source),
                        "windows",
                        expand_user=True,
                    ),
                    "destino": suggest._quote_argument(
                        str(destination),
                        "windows",
                        expand_user=True,
                    ),
                },
            )
            duplicate_copy_result = self.run_recipe_command(
                "copy_file",
                {
                    "origen": suggest._quote_argument(
                        str(source),
                        "windows",
                        expand_user=True,
                    ),
                    "destino": suggest._quote_argument(
                        str(destination),
                        "windows",
                        expand_user=True,
                    ),
                },
            )
            create_result = self.run_recipe_command(
                "create_directory",
                {
                    "dir": suggest._quote_argument(
                        str(new_directory),
                        "windows",
                        expand_user=True,
                    ),
                },
            )

            self.assertEqual(
                copy_result.returncode,
                0,
                f"stdout={copy_result.stdout}\nstderr={copy_result.stderr}",
            )
            self.assertEqual(destination.read_text(encoding="utf-8"), "copy fixture")
            self.assertNotEqual(duplicate_copy_result.returncode, 0)
            self.assertEqual(destination.read_text(encoding="utf-8"), "copy fixture")
            self.assertEqual(
                create_result.returncode,
                0,
                f"stdout={create_result.stdout}\nstderr={create_result.stderr}",
            )
            self.assertTrue(new_directory.is_dir())

    def test_search_pattern_is_data_not_a_second_command(self) -> None:
        recipe = next(
            recipe
            for recipe in suggest._load_recipes()
            if recipe["id"] == "find_by_name"
        )
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "must-remain.txt"
            sentinel.write_text("not deleted", encoding="utf-8")

            def execute_search(pattern_value: str) -> subprocess.CompletedProcess[str]:
                command = suggest._render(
                    recipe["comandos"]["windows"]["cmd"],
                    {
                        "dir": suggest._quote_argument(
                            directory,
                            "windows",
                            expand_user=True,
                        ),
                        "patron": suggest._quote_argument(
                            pattern_value,
                            "windows",
                        ),
                    },
                )
                return subprocess.run(
                    gatekeeper._shell_command(
                        command,
                        gatekeeper.Shell.POWERSHELL,
                    ),
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=15,
                )

            normal_result = execute_search("*.txt")
            pattern = (
                f"*.missing'; Remove-Item -LiteralPath '{sentinel}' #"
            )
            execute_search(pattern)
            sentinel_remains = sentinel.exists()

        self.assertTrue(sentinel_remains)
        self.assertEqual(
            normal_result.returncode,
            0,
            f"stdout={normal_result.stdout}\nstderr={normal_result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
