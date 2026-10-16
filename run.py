"""Figure Forge: build characters from the classic TT LEGO games.

    python run.py              its own window (needs pywebview)
    python run.py --browser    in your web browser
    python run.py --serve      only the server; prints the address
"""
import sys
import threading
import webbrowser

from forge import server


def main(argv):
    httpd = server.serve(int(argv[argv.index('--port') + 1]) if '--port' in argv else 0)
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    if '--serve' in argv:
        print(url, flush=True)
        httpd.serve_forever()
        return 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if '--browser' in argv:
        webbrowser.open(url)
        print(url, flush=True)
        threading.Event().wait()
        return 0
    try:
        import webview
    except ImportError:
        print('pywebview is not installed: opening your browser instead', flush=True)
        webbrowser.open(url)
        threading.Event().wait()
        return 0
    webview.create_window('Figure Forge', url, width=1440, height=900, min_size=(1024, 680))
    webview.start()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
