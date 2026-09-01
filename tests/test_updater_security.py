import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_STRIP = "sudo xattr -rd com.apple.quarantine"
CDN_METADATA = "https://dl.lanshuagent.com/tokei/latest.json"
GITHUB_METADATA = "https://api.github.com/repos/chocolatemale/tokei/releases/latest"
GITHUB_DMG = "https://github.com/chocolatemale/tokei/releases/download/v1.0.14/Tokei-v1.0.14.dmg"


class UpdaterSecurityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Tokei updater is macOS-only")
    def test_swift_security_policy(self):
        swiftc = shutil.which("swiftc")
        self.assertIsNotNone(swiftc, "swiftc is required")

        policy = ROOT / "Tokei" / "Sources" / "TokeiUpdateSecurity" / "UpdateSecurity.swift"
        harness = ROOT / "tests" / "swift" / "UpdaterSecurityCheck.swift"

        with tempfile.TemporaryDirectory(prefix="tokei-updater-test-") as temp_dir:
            binary = Path(temp_dir) / "updater-security-check"
            compile_result = subprocess.run(
                [swiftc, str(policy), str(harness), "-o", str(binary)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )

            run_result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stdout + run_result.stderr,
            )
            self.assertIn("Updater security checks passed", run_result.stdout)

    def test_user_facing_docs_do_not_strip_quarantine(self):
        files = [
            ROOT / "README.md",
            ROOT / "site" / "index.html",
            ROOT / "Tokei" / "package.sh",
            ROOT / "Tokei" / "dmg_bg.py",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                QUARANTINE_STRIP,
                text,
                f"{path.relative_to(ROOT)} must not tell users to strip Gatekeeper quarantine",
            )

        installer = (
            ROOT / "Tokei" / "Sources" / "TokeiUpdateSecurity" / "UpdateSecurity.swift"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            '/usr/bin/xattr -cr "$APP_PATH"',
            installer,
            "in-app updater must not strip quarantine xattrs",
        )

    def test_updater_metadata_is_github_not_cdn(self):
        updater = (ROOT / "Tokei" / "Sources" / "Tokei" / "Updater.swift").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(CDN_METADATA, updater)
        self.assertIn(GITHUB_METADATA, updater)

        policy = (
            ROOT / "Tokei" / "Sources" / "TokeiUpdateSecurity" / "UpdateSecurity.swift"
        ).read_text(encoding="utf-8")
        hosts_block = policy.split("metadataHosts", 1)[1].split("downloadSourceHosts", 1)[0]
        self.assertNotIn("dl.lanshuagent.com", hosts_block)

    def test_release_metadata_includes_dmg_sha256(self):
        generator = ROOT / "Tokei" / "generate_update_metadata.sh"

        with tempfile.TemporaryDirectory(prefix="tokei-metadata-test-") as temp_dir:
            dmg = Path(temp_dir) / "Tokei.dmg"
            output = Path(temp_dir) / "latest.json"
            dmg.write_bytes(b"test-dmg")

            result = subprocess.run(
                ["/bin/bash", str(generator), "v1.0.14", str(dmg), str(output)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            metadata = json.loads(output.read_text())
            self.assertEqual(metadata["tag_name"], "v1.0.14")
            self.assertEqual(metadata["download_url"], GITHUB_DMG)
            self.assertEqual(metadata["sha256"], hashlib.sha256(b"test-dmg").hexdigest())
            self.assertNotIn("dl.lanshuagent.com", metadata["download_url"])


if __name__ == "__main__":
    unittest.main()
