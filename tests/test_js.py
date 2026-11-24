"""Tarayıcı betiklerinin saf mantığı (public/static/liste.js): Node'un test aracıyla."""
import shutil
import subprocess

import pytest

from tests.conftest import KOK


def test_js_testleri():
    node = shutil.which('node')
    if not node:
        pytest.skip('node kurulu değil (tests/js atlandı)')
    r = subprocess.run([node, '--test', str(KOK / 'tests' / 'js')], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
