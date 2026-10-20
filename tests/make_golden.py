"""Re-generate tests/golden/synthetic.json.

The committed golden file was produced by the ORIGINAL Tkinter pipeline of HOPS v3.3.8 (before the
Tk windows were switched to hops.core), so the tests prove that the refactoring did not change any
result. Only re-generate it on purpose, after an intended change of the algorithms:

    .venv/bin/python tests/make_golden.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import conftest  # noqa: E402  (sets HOME / MPLBACKEND before HOPS is imported)
import hops_testlib as t  # noqa: E402


def main():
    directory = tempfile.mkdtemp(prefix='hops_golden_')
    print('Synthetic data in', directory)
    t.generate(directory)
    log, result = conftest.run_core_pipeline(directory)
    result['source'] = 'hops.core, HOPS v{0}'.format(log.version)
    t.save_golden('synthetic.json', result)
    print('Golden file written:', os.path.join(t.GOLDEN_DIR, 'synthetic.json'))


if __name__ == '__main__':
    main()
