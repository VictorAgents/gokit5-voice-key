# SPDX-License-Identifier: GPL-3.0-or-later
import hmac
import socket
import threading
import time
import unittest

import receiver as r


class DummyAudio:
    def __init__(self):
        self.received = 0
        self.cleared = False

    def start(self): pass
    def push(self, data): self.received += len(data)
    def drain(self): pass
    def clear(self): self.cleared = True


class DummyAlt:
    def __init__(self): self.events = []
    def set(self, down): self.events.append(down)


class ProtocolChecks(unittest.TestCase):
    def test_fragmented_frame(self):
        a, b = socket.socketpair()
        packet = r.HEADER.pack(b'GK5P', 1, 5, 4)+b'abcd'
        def sender():
            with b:
                for byte in packet:
                    b.sendall(bytes([byte]))
        worker = threading.Thread(target=sender)
        worker.start()
        with a:
            self.assertEqual(r.receive_frame(a), (5, b'abcd'))
        worker.join()

    def test_oversized_frame_rejected(self):
        a, b = socket.socketpair()
        with a, b:
            b.sendall(r.HEADER.pack(b'GK5P', 1, 5, 65535))
            with self.assertRaises(r.ProtocolError):
                r.receive_frame(a)

    def exercise_session(self, correct_key):
        key, nonce = b'k'*32, b'n'*16
        a, b = socket.socketpair()
        audio, alt = DummyAudio(), DummyAlt()
        errors = []
        def device():
            try:
                with b:
                    r.send_frame(b, 1, nonce)
                    kind, proof = r.receive_frame(b)
                    if kind != 2 or proof != hmac.digest(key, nonce+b'client', 'sha256'):
                        raise AssertionError('Client proof mismatch')
                    r.send_frame(b, 3, hmac.digest(key if correct_key else b'x'*32, nonce+b'device', 'sha256'))
                    if correct_key:
                        r.send_frame(b, 4)
                        r.send_frame(b, 5, bytes(480))
                        time.sleep(.05)
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=device)
        worker.start()
        try:
            with a:
                with self.assertRaises((ConnectionError, r.ProtocolError, OSError)):
                    r.session(a, key, audio, alt, time.monotonic()+2)
        finally:
            worker.join()
        self.assertFalse(errors)
        return audio, alt

    def test_disconnect_releases_owned_shortcut(self):
        audio, alt = self.exercise_session(True)
        self.assertEqual(audio.received, 480)
        self.assertEqual(alt.events, [True, False])
        self.assertTrue(audio.cleared)

    def test_bad_device_proof_never_presses_shortcut(self):
        audio, alt = self.exercise_session(False)
        self.assertEqual(alt.events, [])
        self.assertEqual(audio.received, 0)


if __name__ == '__main__':
    unittest.main()
