import argparse
import hashlib
import os
from pathlib import Path
import signal
import sys
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox
from . import APP_ID
from .library import Library, data_directory


def main():
    parser = argparse.ArgumentParser(description="Linux gaming soundboard")
    parser.add_argument("--import-starters", action="store_true")
    parser.add_argument("--screenshot", metavar="PNG", help="Render UI without connecting audio or shortcuts")
    args = parser.parse_args()
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Game Soundboard")
    app.setDesktopFileName(APP_ID)
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / "app.lock"))
    lock.setStaleLockTime(0)
    socket_name = "game-soundboard-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    if not lock.tryLock(100):
        sock = QLocalSocket()
        sock.connectToServer(socket_name)
        if sock.waitForConnected(1000):
            sock.write(b"show")
            sock.waitForBytesWritten(1000)
            return 0
        print("Soundboard is already running or its library is locked.", file=sys.stderr)
        return 1
    try:
        library = Library(root)
    except Exception as exc:
        QMessageBox.critical(None, "Cannot open soundboard library", str(exc))
        return 1
    if args.import_starters:
        from .starters import STARTERS, download_starter
        failed = False
        for entry in STARTERS:
            if entry[0] not in library.seeded:
                try:
                    record = download_starter(library, entry)
                    library.commit(record)
                    print(f'Imported {record["name"]}: {root / record["cache"]}', flush=True)
                except Exception as exc:
                    failed = True
                    print(f"Failed {entry[1]}: {exc}", file=sys.stderr)
        return int(failed)
    from .ui import Window
    window = Window(library, live=not bool(args.screenshot))
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(socket_name)  # We hold the per-library process lock.
    server.listen(socket_name)
    def activate():
        sock = server.nextPendingConnection()
        window.showNormal()
        window.raise_()
        window.activateWindow()
        if sock:
            sock.disconnectFromServer()
            sock.deleteLater()
    server.newConnection.connect(activate)
    signal.signal(signal.SIGINT, lambda *_: window.close())
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(250)
    window.show()
    if args.screenshot:
        def screenshot():
            window.grab().save(args.screenshot)
            window.close()
        QTimer.singleShot(500, screenshot)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
