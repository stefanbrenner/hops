
import os
import sys
import socket
import argparse
import threading
import webbrowser

# the Tk backend must never be selected in the server process
os.environ['MPLBACKEND'] = 'Agg'


def free_port(preferred):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(('127.0.0.1', preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main(argv=None):

    parser = argparse.ArgumentParser(prog='python -m hops.web', description='HOPS web interface')
    parser.add_argument('--port', type=int, default=8737)
    parser.add_argument('--no-browser', action='store_true', help='do not open the browser automatically')
    args = parser.parse_args(argv)

    import matplotlib
    matplotlib.use('Agg')
    import uvicorn

    port = free_port(args.port)
    url = 'http://127.0.0.1:{0}'.format(port)

    print('\nLoading HOPS... the interface will be available at {0}\n'.format(url))

    from hops.web.server import app, get_state
    get_state()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # open WebSockets must not keep the server from stopping
    uvicorn.run(app, host='127.0.0.1', port=port, log_level=os.environ.get('HOPS_LOG_LEVEL', 'warning'),
                timeout_graceful_shutdown=2)


if __name__ == '__main__':
    sys.exit(main())
