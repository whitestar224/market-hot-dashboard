import os
import subprocess
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from alert_audio import speech_script
import desktop_alert


class AlertAudioTests(unittest.TestCase):
    def test_chime_and_entire_speech_share_one_cross_process_lock(self):
        script = speech_script(123)
        self.assertIn('Local\\XingYunShe.PopupAudio.v1', script)
        self.assertLess(script.index('WaitOne'), script.index("::PlaySound('SystemNotification'"))
        self.assertIn("'SystemNotification', [IntPtr]::Zero, 0x10000", script)
        self.assertIn('MessageBeep(0x40)', script)
        self.assertNotIn('[Console]::Beep', script)
        self.assertLess(script.index('.Speak('), script.index('.ReleaseMutex('))
        self.assertIn('AbandonedMutexException', script)
        self.assertIn('[Console]::In.ReadToEnd()', script)
        self.assertNotIn('SpeakAsync', script)
        self.assertIn('$alertSpeechParentId = 123;', script)

    def test_muted_alert_does_not_start_audio_worker(self):
        with patch.object(desktop_alert.threading, 'Thread') as thread:
            desktop_alert.speak_text('muted', False)
            desktop_alert.play_sound(False)
        thread.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows session mutex')
    def test_two_real_silent_processes_cannot_overlap(self):
        # Replace ALL audio instructions. This integration test never emits sound.
        script = speech_script(os.getpid())
        start = script.index('    # BEGIN POPUP AUDIO')
        end = script.index('} finally {', start)
        critical = '''
    [Console]::WriteLine([DateTime]::UtcNow.Ticks);
    Start-Sleep -Milliseconds 600;
    [Console]::WriteLine([DateTime]::UtcNow.Ticks);
'''
        script = script[:start] + critical + script[end:]
        script = script.replace('XingYunShe.PopupAudio.v1', 'XingYunShe.AudioTest.' + uuid.uuid4().hex)
        self.assertNotIn('Beep', script)
        self.assertNotIn('PlaySound', script)
        self.assertNotIn('.Speak(', script)

        def run():
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                    input='', text=True, capture_output=True, timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(values), 2, result.stderr)
            return values

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: run(), range(2)))
        self.assertTrue(first[1] <= second[0] or second[1] <= first[0], (first, second))


if __name__ == '__main__':
    unittest.main()
