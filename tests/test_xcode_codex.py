import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('upgrade', Path(__file__).parents[1] / 'scripts/xcode_codex.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class UpgradeTests(unittest.TestCase):
    def test_top_level_model_only(self):
        original = 'model = "old"\nservice_tier = "flex"\n[profiles.other]\nmodel = "keep"\n'
        updated = u.model_config(original, 'gpt-6.1-sol')
        self.assertEqual(updated, original.replace('"old"', '"gpt-6.1-sol"'))

    def test_missing_model_is_inserted_before_tables(self):
        self.assertEqual(u.model_config('[profile]\nmodel = "keep"\n', 'gpt-6.1-sol'),
                         'model = "gpt-6.1-sol"\n[profile]\nmodel = "keep"\n')

    def test_duplicate_models_rejected(self):
        with self.assertRaises(ValueError):
            u.model_config('model="one"\nmodel="two"\n', 'gpt-6.1-sol')

    def test_quoted_model_key(self):
        self.assertEqual(u.model_config('"model" = "old"\n', 'gpt-6.1-sol'),
                         'model = "gpt-6.1-sol"\n')

    def test_multiline_instructions_preserved(self):
        original = 'developer_instructions = """\n[not a table]\nmodel = "example"\n"""\nmodel = "old"\n'
        updated = u.model_config(original, 'gpt-6.1-sol')
        self.assertEqual(updated, original.replace('model = "old"', 'model = "gpt-6.1-sol"'))

    def test_checksums_require_both_sources(self):
        data = b'package'
        sha = hashlib.sha256(data).hexdigest()
        sums = sha + '  package.tar.gz\n'
        self.assertEqual(u.verify_archive(data, sums, 'sha256:' + sha, 'package.tar.gz'), sha)
        for bad_sums, bad_digest in [(sums, None), ('0' * 64 + '  package.tar.gz', 'sha256:' + sha)]:
            with self.assertRaises(RuntimeError):
                u.verify_archive(data, bad_sums, bad_digest, 'package.tar.gz')

    def test_unsafe_archives_rejected_before_writes(self):
        for name, target in [('../escape', None), ('/escape', None), ('link', '../../escape')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / 'test.tar'
                with tarfile.open(archive, 'w') as t:
                    member = tarfile.TarInfo(name)
                    if target:
                        member.type = tarfile.SYMTYPE
                        member.linkname = target
                    t.addfile(member)
                with self.assertRaises(RuntimeError):
                    u.unpack(archive, Path(directory) / 'out')
                self.assertFalse((Path(directory) / 'out').exists())

    def fixture(self, directory, existing=True):
        base = Path(directory).resolve()
        old = base / 'Agents/codex/old'
        new = base / 'Agents/codex/new'
        old.mkdir(parents=True)
        new.mkdir()
        link = base / 'Agents/XcodeVersions/BUILD/codex'
        link.parent.mkdir(parents=True)
        link.symlink_to(old)
        (base / 'codex').mkdir()
        if existing:
            (base / 'codex/config.toml').write_text('model = "old"\n[tools]\nkeep = true\n')
            (base / 'codex/models_cache.json').write_text('{"previous":true}\n')
        return base, old, new, link

    def test_install_and_exact_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            base, old, new, link = self.fixture(d)
            originals = {n: (base / 'codex' / n).read_bytes() for n in ('config.toml', 'models_cache.json')}
            backup = u.install(base, link, new, 'gpt-6.1-sol')
            self.assertEqual(link.resolve(), new)
            self.assertFalse((base / 'codex/models_cache.json').exists())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
            self.assertIn('gpt-6.1-sol', (base / 'codex/config.toml').read_text())
            u.restore(base, backup)
            self.assertEqual(link.resolve(), old)
            for name, data in originals.items():
                self.assertEqual((base / 'codex' / name).read_bytes(), data)

    def test_rollback_restores_absent_files(self):
        with tempfile.TemporaryDirectory() as d:
            base, old, new, link = self.fixture(d, existing=False)
            backup = u.install(base, link, new, 'gpt-6.1-sol')
            (base / 'codex/models_cache.json').write_text('{}')
            u.restore(base, backup)
            self.assertEqual(link.resolve(), old)
            self.assertFalse((base / 'codex/config.toml').exists())
            self.assertFalse((base / 'codex/models_cache.json').exists())

    def test_failed_switch_automatically_restores_files(self):
        with tempfile.TemporaryDirectory() as d:
            base, old, new, link = self.fixture(d)
            original = (base / 'codex/config.toml').read_bytes()
            real_switch = u.switch_link
            def fail_new(path, target):
                if target == new:
                    raise OSError('simulated switch failure')
                real_switch(path, target)
            with patch.object(u, 'switch_link', side_effect=fail_new):
                with self.assertRaises(OSError):
                    u.install(base, link, new, 'gpt-6.1-sol')
            self.assertEqual(link.resolve(), old)
            self.assertEqual((base / 'codex/config.toml').read_bytes(), original)
            self.assertTrue((base / 'codex/models_cache.json').exists())

    def test_failed_install_removes_new_runtime_for_retry(self):
        with tempfile.TemporaryDirectory() as d:
            base, old, new, link = self.fixture(d)
            new.rmdir()
            stage = base / 'stage'
            stage.mkdir()
            (stage / 'file').write_text('package')
            with patch.object(u, 'install', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    u.publish_and_install(base, link, stage, new, 'gpt-6.1-sol')
            self.assertFalse(new.exists())
            self.assertEqual(link.resolve(), old)
            backup = u.publish_and_install(base, link, stage, new, 'gpt-6.1-sol')
            self.assertTrue(backup.is_dir())
            self.assertEqual(link.resolve(), new)

    def test_dry_run_does_not_fetch_or_modify(self):
        with tempfile.TemporaryDirectory() as d:
            base, old, new, link = self.fixture(d)
            home = Path(d) / 'home'
            coding = home / 'Library/Developer/Xcode/CodingAssistant'
            coding.parent.mkdir(parents=True)
            coding.symlink_to(base)
            before = {str(p.relative_to(base)): p.read_bytes() for p in (base / 'codex').iterdir()}
            with patch.object(u.Path, 'home', return_value=home), patch.object(u.platform, 'system', return_value='Darwin'), patch.object(u.platform, 'machine', return_value='arm64'), patch.object(u.subprocess, 'check_output', return_value='Xcode 27.1\nBuild version BUILD\n'), patch.object(u, 'fetch') as fetch, patch.object(u.sys, 'argv', ['xcode_codex.py']):
                u.main()
                fetch.assert_not_called()
            self.assertEqual(link.resolve(), old)
            self.assertEqual(before, {str(p.relative_to(base)): p.read_bytes() for p in (base / 'codex').iterdir()})


if __name__ == '__main__':
    unittest.main()
