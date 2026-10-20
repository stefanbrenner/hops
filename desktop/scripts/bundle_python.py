"""Build the Python engine that ships inside the desktop app.

    python3 desktop/scripts/bundle_python.py

Creates desktop/src-tauri/resources/engine with
  python/  a standalone CPython (python-build-standalone, installed by uv) with the package versions
           of requirements-lock.txt
  app/     the HOPS sources (including the built web interface)

The app then runs without any Python installation on the user's machine. Needs `uv` and network
access; run it on each platform the app is built for (macOS arm64 / x64, Windows x64).
"""

import os
import sys
import glob
import shutil
import argparse
import platform
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(ROOT, 'desktop', 'src-tauri', 'resources', 'engine')

# the Python and package versions the regression tests were validated with (see requirements-lock.txt)
PYTHON_VERSION = '3.14.8'



def run(*command, **kwargs):
    print('$', ' '.join(command))
    subprocess.run(command, check=True, **kwargs)


def python_executable(python_dir):
    if platform.system() == 'Windows':
        return os.path.join(python_dir, 'python.exe')
    return os.path.join(python_dir, 'bin', 'python3')


def install_python(uv):
    downloads = os.path.join(OUT, '.downloads')
    run(uv, 'python', 'install', PYTHON_VERSION, '--install-dir', downloads, '--no-bin')
    installs = [d for d in glob.glob(os.path.join(downloads, 'cpython-{0}-*'.format(PYTHON_VERSION))) if os.path.isdir(d)]
    if not installs:
        sys.exit('uv did not install CPython {0}'.format(PYTHON_VERSION))
    python_dir = os.path.join(OUT, 'python')
    shutil.copytree(sorted(installs)[-1], python_dir, symlinks=True)
    shutil.rmtree(downloads)
    # the standalone build marks itself as externally managed; this copy belongs to the app
    for marker in glob.glob(os.path.join(python_dir, '**', 'EXTERNALLY-MANAGED'), recursive=True):
        os.remove(marker)
    return python_executable(python_dir)


def install_requirements(uv, python):
    run(uv, 'pip', 'install', '--python', python, '--no-cache', '-r', os.path.join(ROOT, 'requirements-lock.txt'))


def copy_hops():
    if not os.path.isfile(os.path.join(ROOT, 'hops', 'web', 'static', 'index.html')):
        sys.exit('Build the web interface first: npm --prefix hops/web/frontend run build')
    target = os.path.join(OUT, 'app', 'hops')
    shutil.copytree(os.path.join(ROOT, 'hops'), target,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'frontend', '.DS_Store'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app-only', action='store_true', help='only refresh the HOPS sources, keep Python')
    args = parser.parse_args()

    uv = shutil.which('uv')
    if uv is None:
        sys.exit('uv is needed: https://docs.astral.sh/uv/')

    if args.app_only and os.path.isdir(os.path.join(OUT, 'python')):
        shutil.rmtree(os.path.join(OUT, 'app'), ignore_errors=True)
        copy_hops()
        print('HOPS sources refreshed in', OUT)
        return

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    python = install_python(uv)
    install_requirements(uv, python)
    copy_hops()

    # compile once here, so the first start of the app is fast
    run(python, '-m', 'compileall', '-q', '-j', '0', os.path.join(OUT, 'app'),
        os.path.dirname(os.path.dirname(python)))

    # smoke test: the engine imports with the bundled Python only
    env = dict(os.environ, PYTHONPATH=os.path.join(OUT, 'app'), PYTHONNOUSERSITE='1', MPLBACKEND='Agg')
    run(python, '-c', 'import hops.web.server, hops.thirdparty.twirl, astropy, photutils, emcee, exoclock; print("engine ok")',
        env=env, cwd=os.path.expanduser('~'))

    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(OUT) for f in files
               if not os.path.islink(os.path.join(d, f)))
    print('Engine ready in {0} ({1:.0f} MB)'.format(OUT, size / 1024 / 1024))


if __name__ == '__main__':
    main()
