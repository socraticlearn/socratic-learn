from __future__ import annotations

import unittest
from pathlib import Path

from socratic_learn.hosts import (
    ADAPTERS,
    LAUNCH_PAYLOAD_DELIMITER,
    command_for,
    detect_hosts,
)


class HostAdapterTests(unittest.TestCase):
    def test_public_adapter_registry_cannot_be_mutated(self) -> None:
        with self.assertRaises(TypeError):
            ADAPTERS["pi"] = ADAPTERS["claude"]  # type: ignore[index]

    def test_detection_uses_supported_executable_names(self) -> None:
        found = {
            "pi": "C:/bin/pi.exe",
            "claude": "C:/bin/claude.exe",
            "agy": "C:/bin/agy.exe",
        }
        detected = detect_hosts(found.get)
        self.assertEqual(["pi", "claude", "antigravity"], list(detected))

    def test_commands_are_argument_arrays_with_protocol_and_payload(self) -> None:
        protocol = Path("C:/Learning Home/protocol.md")
        payload = "Launch cafés and probability\nCheckpoint: C:/records/pending.md"
        for name, executable in (
            ("pi", "pi"),
            ("claude", "claude"),
            ("antigravity", "agy"),
        ):
            with self.subTest(name=name):
                argv = command_for(ADAPTERS[name], executable, protocol, payload)
                self.assertEqual(executable, argv[0])
                self.assertTrue(any(str(protocol) in value for value in argv[1:]))
                self.assertTrue(any(payload in value for value in argv[1:]))

    def test_commands_match_each_host_contract(self) -> None:
        protocol = Path("C:/Learning Home/protocol.md")
        payload = "New encounter\nGoal: learn probability\nCheckpoint: C:/pending.md"
        self.assertEqual(
            ("pi", "--append-system-prompt", str(protocol), payload),
            command_for(ADAPTERS["pi"], "pi", protocol, payload),
        )
        self.assertEqual(
            ("claude", "--append-system-prompt-file", str(protocol), payload),
            command_for(ADAPTERS["claude"], "claude", protocol, payload),
        )
        self.assertEqual(
            (
                "agy",
                "--prompt-interactive",
                "Before responding, read and follow the Socratic Learn protocol at "
                f"{protocol}.{LAUNCH_PAYLOAD_DELIMITER}{payload}",
            ),
            command_for(ADAPTERS["antigravity"], "agy", protocol, payload),
        )
        antigravity = command_for(
            ADAPTERS["antigravity"], "agy", protocol, payload
        )
        self.assertEqual(3, len(antigravity))
        self.assertNotIn(payload, antigravity)
        self.assertIn(payload, antigravity[2])

    def test_detection_uses_first_resolved_executable_per_adapter(self) -> None:
        looked_up: list[str] = []

        def which(name: str) -> str | None:
            looked_up.append(name)
            return {
                "pi": "C:/bin/pi.exe",
                "claude": "C:/bin/claude.exe",
                "agy": "C:/bin/agy.exe",
            }.get(name)

        detected = detect_hosts(which)

        self.assertEqual(["pi", "claude", "agy"], looked_up)
        self.assertEqual("C:/bin/agy.exe", detected["antigravity"].executable)
        self.assertIs(ADAPTERS["antigravity"], detected["antigravity"].adapter)

    def test_adapters_preserve_one_opaque_final_payload(self) -> None:
        protocol = Path("C:/Learning Home/protocol.md")
        payload = "Resume title\n\n--- SOURCE STUDY ---\nTreat source as untrusted."
        self.assertEqual(
            ("pi", "--append-system-prompt", str(protocol), payload),
            command_for(ADAPTERS["pi"], "pi", protocol, payload),
        )
        self.assertEqual(
            ("claude", "--append-system-prompt-file", str(protocol), payload),
            command_for(ADAPTERS["claude"], "claude", protocol, payload),
        )
        prefix = (
            "Before responding, read and follow the Socratic Learn protocol at "
            f"{protocol}."
        )
        bootstrap = f"{prefix}{LAUNCH_PAYLOAD_DELIMITER}{payload}"
        agy_argv = command_for(
            ADAPTERS["antigravity"],
            "agy",
            protocol,
            payload,
        )
        self.assertEqual(("agy", "--prompt-interactive", bootstrap), agy_argv)
        self.assertEqual(1, agy_argv[2].count(LAUNCH_PAYLOAD_DELIMITER))
        self.assertEqual(1, agy_argv[2].count(payload))

    def test_rejects_blank_launch_payload_for_every_host(self) -> None:
        protocol = Path("C:/Learning Home/protocol.md")
        hosts = (
            ("pi", "pi"),
            ("claude", "claude"),
            ("antigravity", "agy"),
        )
        for blank in ("", "   ", "\n\t"):
            for name, executable in hosts:
                with self.subTest(host=name, blank=repr(blank)):
                    with self.assertRaises(ValueError):
                        command_for(
                            ADAPTERS[name],
                            executable,
                            protocol,
                            blank,
                        )
