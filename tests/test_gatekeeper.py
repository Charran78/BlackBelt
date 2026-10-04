import platform
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from blackbelt.core import gatekeeper


POWERSHELL_AVAILABLE = any(
    shutil.which(executable)
    for executable in ("powershell.exe", "powershell", "pwsh.exe", "pwsh")
)
BASH_AVAILABLE = shutil.which("bash") is not None


class ShellDetectionTests(unittest.TestCase):
    @patch("blackbelt.core.gatekeeper.platform.system", return_value="Windows")
    def test_detects_powershell_on_windows(self, _system: Mock) -> None:
        self.assertIs(gatekeeper.detect_shell(), gatekeeper.Shell.POWERSHELL)

    @patch("blackbelt.core.gatekeeper.platform.system", return_value="Linux")
    def test_detects_bash_on_linux(self, _system: Mock) -> None:
        self.assertIs(gatekeeper.detect_shell(), gatekeeper.Shell.BASH)


class RiskAnalysisTests(unittest.TestCase):
    def test_bash_risk_levels(self) -> None:
        cases = (
            ("rm -rf ./temporary", gatekeeper.Risk.CRITICAL),
            (
                "find /tmp -type f -exec rm -- {} +",
                gatekeeper.Risk.CRITICAL,
            ),
            ("git reset --soft HEAD~1", gatekeeper.Risk.CRITICAL),
            ("sudo apt update", gatekeeper.Risk.CRITICAL),
            ("touch ./note.txt", gatekeeper.Risk.WARN),
            ("printf 'hello'", gatekeeper.Risk.SAFE),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertIs(
                    gatekeeper.analyze_command(command, gatekeeper.Shell.BASH),
                    expected,
                )

    def test_powershell_risk_levels(self) -> None:
        cases = (
            ("Remove-Item -Recurse .\\temporary", gatekeeper.Risk.CRITICAL),
            ("RM .\\temporary", gatekeeper.Risk.CRITICAL),
            ("Invoke-Expression $script", gatekeeper.Risk.CRITICAL),
            ("git reset --soft HEAD~1", gatekeeper.Risk.CRITICAL),
            (
                "powershell.exe -Command Remove-Item .\\temporary",
                gatekeeper.Risk.CRITICAL,
            ),
            ("Set-Content .\\note.txt 'hello'", gatekeeper.Risk.WARN),
            ("Get-Process", gatekeeper.Risk.SAFE),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertIs(
                    gatekeeper.analyze_command(
                        command,
                        gatekeeper.Shell.POWERSHELL,
                    ),
                    expected,
                )

    def test_unknown_shell_fails_closed(self) -> None:
        self.assertIs(
            gatekeeper.analyze_command("echo harmless", shell="unknown"),
            gatekeeper.Risk.CRITICAL,
        )
        self.assertIs(
            gatekeeper.analyze_command("echo harmless", shell=object()),
            gatekeeper.Risk.CRITICAL,
        )


class ShellCommandTests(unittest.TestCase):
    @patch("blackbelt.core.gatekeeper.shutil.which")
    def test_builds_native_powershell_command(self, which: Mock) -> None:
        which.side_effect = lambda name: (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            if name == "powershell.exe"
            else None
        )

        command = gatekeeper._shell_command(
            "Write-Output 'hola'",
            gatekeeper.Shell.POWERSHELL,
        )

        self.assertTrue(command[0].endswith("powershell.exe"))
        self.assertIn("-NoProfile", command)
        self.assertIn("-Command", command)
        self.assertIn("Write-Output 'hola'", command[-1])
        self.assertIn("[Console]::OutputEncoding", command[-1])

    @patch("blackbelt.core.gatekeeper.shutil.which", return_value="bash")
    def test_builds_bash_command_without_intermediary_shell(
        self,
        _which: Mock,
    ) -> None:
        self.assertEqual(
            gatekeeper._shell_command("printf hello", gatekeeper.Shell.BASH),
            ["bash", "-c", "printf hello"],
        )

    @patch("blackbelt.core.gatekeeper.subprocess.run")
    @patch("blackbelt.core.gatekeeper._shell_command")
    @patch("blackbelt.core.gatekeeper.confirm", return_value=True)
    @patch("blackbelt.core.gatekeeper.detect_shell")
    def test_run_command_returns_process_result(
        self,
        detect_shell: Mock,
        _confirm: Mock,
        shell_command: Mock,
        run: Mock,
    ) -> None:
        detect_shell.return_value = gatekeeper.Shell.POWERSHELL
        shell_command.return_value = ["powershell.exe", "-Command", "Write-Output ok"]
        run.return_value = subprocess.CompletedProcess(
            args=["powershell.exe"],
            returncode=7,
            stdout="salida",
            stderr="error",
        )

        result = gatekeeper.run_command("Get-Process")

        self.assertEqual(result, (7, "salida", "error"))
        run.assert_called_once()
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("blackbelt.core.gatekeeper._shell_command", side_effect=FileNotFoundError)
    @patch("blackbelt.core.gatekeeper.confirm", return_value=True)
    @patch("blackbelt.core.gatekeeper.detect_shell", return_value=gatekeeper.Shell.BASH)
    def test_missing_shell_returns_exit_code_127(
        self,
        _detect_shell: Mock,
        _confirm: Mock,
        _shell_command: Mock,
    ) -> None:
        result = gatekeeper.run_command("printf hello")
        self.assertEqual(result[0], 127)
        self.assertIn("Comando no encontrado", result[2])

    def test_empty_command_is_rejected(self) -> None:
        with patch("blackbelt.core.gatekeeper.confirm") as confirm:
            result = gatekeeper.run_command("  ")
        self.assertEqual(result, (2, "", "Falta el comando a ejecutar."))
        confirm.assert_not_called()

    @patch("blackbelt.core.gatekeeper.confirm", return_value=False)
    @patch(
        "blackbelt.core.gatekeeper.detect_shell",
        return_value=gatekeeper.Shell.BASH,
    )
    def test_minimum_risk_cannot_be_lowered_by_command_analysis(
        self,
        _detect_shell: Mock,
        confirm: Mock,
    ) -> None:
        result = gatekeeper.run_command(
            "printf harmless",
            minimum_risk=gatekeeper.Risk.CRITICAL,
        )

        self.assertEqual(result[0], -1)
        self.assertIs(confirm.call_args.args[0], gatekeeper.Risk.CRITICAL)


@unittest.skipUnless(
    platform.system() == "Windows" and POWERSHELL_AVAILABLE,
    "Requiere PowerShell en Windows.",
)
class PowerShellIntegrationTests(unittest.TestCase):
    @patch("blackbelt.core.gatekeeper.confirm", return_value=True)
    def test_runs_powershell_and_preserves_utf8_output(
        self,
        _confirm: Mock,
    ) -> None:
        result = gatekeeper.run_command("Write-Output 'BlackBelt integración ñ'")
        self.assertEqual(result[0], 0, result[2])
        self.assertIn("BlackBelt integración ñ", result[1])

    @patch("blackbelt.core.gatekeeper.confirm", return_value=True)
    def test_preserves_native_process_exit_code(self, _confirm: Mock) -> None:
        result = gatekeeper.run_command("cmd.exe /c exit 7")
        self.assertEqual(result[0], 7, result[2])


@unittest.skipUnless(
    platform.system() != "Windows" and BASH_AVAILABLE,
    "Requiere Bash en Linux o macOS.",
)
class BashIntegrationTests(unittest.TestCase):
    @patch("blackbelt.core.gatekeeper.confirm", return_value=True)
    def test_runs_bash_command(self, _confirm: Mock) -> None:
        result = gatekeeper.run_command("printf 'BlackBelt integration\\n'")
        self.assertEqual(result[0], 0, result[2])
        self.assertIn("BlackBelt integration", result[1])


if __name__ == "__main__":
    unittest.main()
