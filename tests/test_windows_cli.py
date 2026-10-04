import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

import ollama
from typer.testing import CliRunner

from blackbelt import main
from blackbelt.core import environment
from blackbelt.tools import windows_mentor


runner = CliRunner()


class WindowsEnvironmentTests(unittest.TestCase):
    @patch("blackbelt.core.environment.platform.system", return_value="Windows")
    def test_detects_powershell_from_its_environment(
        self,
        _system: Mock,
    ) -> None:
        with patch.dict(
            os.environ,
            {"PSModulePath": r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules"},
            clear=True,
        ):
            self.assertEqual(environment.detect_shell(), "PowerShell")

    @patch("blackbelt.core.environment.platform.system", return_value="Windows")
    def test_detects_command_prompt(
        self,
        _system: Mock,
    ) -> None:
        with patch.dict(os.environ, {"PROMPT": "$P$G"}, clear=True):
            self.assertEqual(environment.detect_shell(), "Command Prompt")


class WindowsToolTests(unittest.TestCase):
    @patch("blackbelt.tools.windows_mentor.ollama.chat")
    def test_displays_summary_from_typed_ollama_sdk_response(
        self,
        chat: Mock,
    ) -> None:
        chat.return_value = ollama.ChatResponse(
            model="qwen2.5-coder:0.5b",
            created_at="2026-10-01T00:00:00Z",
            done=True,
            message=ollama.Message(
                role="assistant",
                content="Lista los elementos ocultos y del sistema.",
            ),
        )
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._summarize_reference(
                "El parámetro Force incluye elementos ocultos y del sistema.",
            )

        self.assertTrue(
            any(
                "Resumen generado a partir de la ayuda oficial:"
                in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        self.assertTrue(
            any(
                "Lista los elementos ocultos y del sistema."
                in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        self.assertEqual(chat.call_args.kwargs["options"]["temperature"], 0)
        self.assertLessEqual(chat.call_args.kwargs["options"]["num_predict"], 64)

    @patch("blackbelt.tools.windows_mentor.subprocess.run")
    @patch(
        "blackbelt.tools.windows_mentor.shutil.which",
        return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    @patch("blackbelt.tools.windows_mentor.ollama.chat")
    def test_explains_using_local_powershell_help(
        self,
        chat: Mock,
        _which: Mock,
        run_process: Mock,
    ) -> None:
        run_process.return_value = subprocess.CompletedProcess(
            args=["powershell.exe"],
            returncode=0,
            stdout=json.dumps(
                {
                    "Name": "Get-ChildItem",
                    "Synopsis": "Gets child items.",
                    "Description": "Gets items in the specified location.",
                    "Parameters": [
                        {
                            "Name": "Force",
                            "Description": "Gets hidden or system files.",
                        }
                    ],
                    "Incomplete": False,
                }
            ),
            stderr="",
        )
        chat.return_value = {
            "message": {"content": "Lista los elementos, incluidos los ocultos."}
        }
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor.run(["explain", "Get-ChildItem", "-Force"])

        command = run_process.call_args.args[0]
        self.assertTrue(command[0].endswith("powershell.exe"))
        self.assertIn("Get-Help -Name 'Get-ChildItem' -Full", command[-1])
        self.assertIn("$requested = @('Force')", command[-1])
        self.assertNotIn("'Recurse'", command[-1])
        prompt = chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Gets hidden or system files.", prompt)
        self.assertNotIn("Recurse", prompt)
        self.assertNotIn("specified location", prompt)
        self.assertIn("una sola frase", prompt)
        self.assertLessEqual(chat.call_args.kwargs["options"]["num_predict"], 64)
        self.assertEqual(chat.call_args.kwargs["options"]["temperature"], 0)
        self.assertTrue(
            any(
                "Get-ChildItem -Force" in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        self.assertTrue(
            any(
                "Gets hidden or system files." in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        self.assertTrue(
            any(
                "Resumen generado a partir de la ayuda oficial:" in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        self.assertEqual(run_process.call_args.kwargs["timeout"], 15)

    @patch("blackbelt.tools.windows_mentor.subprocess.run")
    @patch("blackbelt.tools.windows_mentor.shutil.which", return_value="powershell.exe")
    @patch("blackbelt.tools.windows_mentor.ollama.chat")
    def test_warns_when_local_help_is_incomplete(
        self,
        chat: Mock,
        _which: Mock,
        run_process: Mock,
    ) -> None:
        run_process.return_value = subprocess.CompletedProcess(
            args=["powershell.exe"],
            returncode=0,
            stdout=json.dumps(
                {
                    "Name": "Get-ChildItem",
                    "Synopsis": "Gets child items.",
                    "Description": "Partial local help.",
                    "Parameters": [],
                    "Incomplete": True,
                }
            ),
            stderr="",
        )
        chat.return_value = {
            "message": {"content": "La documentación local es parcial."}
        }
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._explain("Get-ChildItem -Force")

        self.assertTrue(
            any(
                "La ayuda local está marcada como incompleta."
                in str(call.args[0])
                for call in print_output.call_args_list
            )
        )
        chat.assert_called_once()

    @patch("blackbelt.tools.windows_mentor.subprocess.run")
    @patch("blackbelt.tools.windows_mentor.shutil.which", return_value="powershell.exe")
    def test_rejects_command_name_that_could_inject_powershell(
        self,
        _which: Mock,
        run_process: Mock,
    ) -> None:
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._explain("Get-ChildItem'; Remove-Item *")

        run_process.assert_not_called()
        self.assertIn("No puedo consultar ayuda", print_output.call_args.args[0])

    @patch("blackbelt.tools.windows_mentor.subprocess.run")
    @patch("blackbelt.tools.windows_mentor.shutil.which", return_value="powershell.exe")
    def test_rejects_pipelines_instead_of_summarizing_only_part(
        self,
        _which: Mock,
        run_process: Mock,
    ) -> None:
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._explain("Get-ChildItem -Force | Remove-Item")

        run_process.assert_not_called()
        self.assertIn("un solo comando", print_output.call_args.args[0])

    def test_bounds_and_normalizes_structured_help(self) -> None:
        reference = windows_mentor._parse_help_reference(
            json.dumps(
                {
                    "Name": "Example",
                    "Synopsis": "S" * 800,
                    "Description": "D" * 2000,
                    "Parameters": {
                        "Name": "Force",
                        "Description": "P" * 2000,
                    },
                    "Incomplete": False,
                }
            )
        )

        self.assertEqual(len(reference.synopsis), 500)
        self.assertEqual(
            len(reference.description),
            windows_mentor.MAX_REFERENCE_CHARS,
        )
        self.assertEqual(len(reference.parameters), 1)
        self.assertEqual(
            len(reference.parameters[0][1]),
            windows_mentor.MAX_REFERENCE_CHARS,
        )

    @patch(
        "blackbelt.tools.windows_mentor.ollama.chat",
        side_effect=ollama.RequestError("offline"),
    )
    def test_keeps_source_visible_when_ollama_is_unavailable(
        self,
        _chat: Mock,
    ) -> None:
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._summarize_reference(
                "Descripción oficial: muestra elementos ocultos.",
            )

        self.assertIn(
            "fuente mostrada arriba",
            print_output.call_args.args[0],
        )

    @patch("blackbelt.tools.windows_mentor.ollama.chat")
    def test_warns_when_model_stops_at_output_limit(
        self,
        chat: Mock,
    ) -> None:
        chat.return_value = ollama.ChatResponse(
            model="qwen2.5-coder:0.5b",
            created_at="2026-10-01T00:00:00Z",
            done=True,
            done_reason="length",
            message=ollama.Message(
                role="assistant",
                content="La opción permite mostrar elementos ocultos, pero no",
            ),
        )
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._summarize_reference(
                "Force allows getting hidden files.",
            )

        rendered = "\n".join(
            str(call.args[0]) for call in print_output.call_args_list
        )
        self.assertIn("no terminó el resumen", rendered)
        self.assertNotIn("Resumen generado a partir", rendered)

    @patch("blackbelt.tools.windows_mentor.ollama.chat")
    def test_rejects_overlong_model_summary(
        self,
        chat: Mock,
    ) -> None:
        chat.return_value = ollama.ChatResponse(
            model="qwen2.5-coder:0.5b",
            created_at="2026-10-01T00:00:00Z",
            done=True,
            message=ollama.Message(
                role="assistant",
                content="palabra " * (windows_mentor.MAX_SUMMARY_WORDS + 1),
            ),
        )
        with patch.object(windows_mentor.console, "print") as print_output:
            windows_mentor._summarize_reference(
                "Comando: Get-ChildItem -Force\n"
                "Parámetro -Force: Gets hidden or system files."
            )

        rendered = "\n".join(
            str(call.args[0]) for call in print_output.call_args_list
        )
        self.assertIn("demasiado largo", rendered)
        self.assertNotIn("Resumen generado a partir", rendered)


class CommandLineUsabilityTests(unittest.TestCase):
    def test_tools_menu_lists_windows_without_colored_descriptions(self) -> None:
        result = runner.invoke(main.app, ["tools"], color=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("windows", result.output)
        self.assertIn(
            "Mentor Windows: informacion y comandos PowerShell",
            result.output,
        )
        self.assertNotIn("\x1b[", result.output)

    def test_run_without_tool_shows_menu_and_usage(self) -> None:
        result = runner.invoke(main.app, ["run"], color=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Herramientas registradas", result.output)
        self.assertIn(
            "blackbelt run <herramienta> argumentos...",
            result.output,
        )
        self.assertNotIn("Missing argument", result.output)

    def test_run_env_points_to_top_level_command(self) -> None:
        result = runner.invoke(main.app, ["run", "env"], color=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Usa: blackbelt env", result.output)
        self.assertNotIn("Herramienta env no encontrada", result.output)

    def test_root_env_reports_detected_powershell(self) -> None:
        with patch(
            "blackbelt.core.environment.detect_shell",
            return_value="PowerShell",
        ):
            result = runner.invoke(main.app, ["env"], color=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("PowerShell", result.output)


if __name__ == "__main__":
    unittest.main()
