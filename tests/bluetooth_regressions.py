"""真实 L2CAP 连接边界回归，由 test_bluetooth.py 的虚拟 HCI 环境调用。"""
import os
from pathlib import Path
import select
import signal
import socket
import time

SOL_BLUETOOTH = getattr(socket, 'SOL_BLUETOOTH', 274)


def queued_offline_frames(start, root, frame, local, peer, mode):
    """同时唤醒连接事件与UDP事件，确保离线队列不会在上线后重放。"""
    resources = []
    adapter = None
    paused = False
    psm = 0x1005

    def udp_socket():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        resources.append(sock)
        sock.bind(('127.0.0.1', 0))
        sock.settimeout(3)
        return sock

    def bluetooth_socket():
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        resources.append(sock)
        sock.setsockopt(SOL_BLUETOOTH, 4, b'\x01\x00')  # BT_SECURITY_LOW
        sock.settimeout(5)
        return sock

    def pause():
        nonlocal paused
        os.kill(adapter.p.pid, signal.SIGSTOP)
        paused = True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = (Path('/proc')/str(adapter.p.pid)/'status').read_text()
            if any(row.startswith('State:') and row.split()[1] in ('T', 't')
                   for row in state.splitlines()):
                return
            time.sleep(.01)
        raise AssertionError('未能暂停测试适配器')

    try:
        app = udp_socket()
        reservation = udp_socket()
        udp_port = reservation.getsockname()[1]
        reservation.close()

        if mode == 'connect':
            listener = bluetooth_socket()
            # 内核暂缓完成握手；accept后才能确定被测客户端正在connecting。
            listener.bind((peer, psm))
            listener.setsockopt(SOL_BLUETOOTH, 7, 1)  # BT_DEFER_SETUP
            listener.listen(1)
        adapter = start('queued-offline-'+mode,
                        [root/'bin/bt-channel', mode, local, peer, hex(psm),
                         udp_port, app.getsockname()[1], 'low'])
        adapter.wait('[READY]')
        if mode == 'connect':
            remote, _ = listener.accept()
            resources.append(remote)
            remote.settimeout(3)
            assert not any('[BT] connected' in row for row in adapter.lines)
        pause()

        stale = [frame(0x0A01, 0x0B01, 'queued_offline_%d' % i) for i in range(4)]
        for packet in stale:
            app.sendto(packet, ('127.0.0.1', udp_port))
        app.sendto(stale[0]+b'trailing', ('127.0.0.1', udp_port))
        other = udp_socket()
        other.sendto(stale[0], ('127.0.0.1', udp_port))

        if mode == 'listen':
            remote = bluetooth_socket()
            remote.bind((peer, 0))
            remote.connect((local, psm))  # 服务端暂停时，内核完成连接并放入accept队列。
        else:
            remote.setblocking(False)
            try:
                remote.recv(1)  # BT_DEFER_SETUP: 触发内核完成握手，不收取业务数据。
            except BlockingIOError:
                pass
            assert select.select([], [remote], [], 3)[1], '延迟握手未完成'
            remote.settimeout(3)

        os.kill(adapter.p.pid, signal.SIGCONT)
        paused = False
        adapter.wait('[BT] connected')
        live = frame(0x0A01, 0x0B01, 'online_'+mode)
        app.sendto(live, ('127.0.0.1', udp_port))
        received = []
        while True:
            data = remote.recv(4096)
            if data == live:
                break
            received.append(data)
            assert data and len(received) <= len(stale), '收到非预期帧'
        assert not received, '%s重放了%d条离线帧' % (mode, len(received))
        reverse = frame(0x0B01, 0x0A01, 'online_reverse_'+mode)
        remote.sendall(reverse)
        assert app.recv(4096) == reverse
        os.kill(adapter.p.pid, signal.SIGUSR1)
        counters = adapter.wait('[STATS]')
        assert 'offline=4 ' in counters, counters
        assert 'invalid=1 denied=1 ' in counters, counters
        return {'offline_queued': len(stale), 'replayed': 0,
                'online_both_directions': True, 'counters': counters}
    finally:
        if adapter is not None:
            if paused:
                os.kill(adapter.p.pid, signal.SIGCONT)
            adapter.stop()
        for sock in reversed(resources):
            sock.close()
