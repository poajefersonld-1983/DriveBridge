import os
import tempfile
import time
try:
    import tkinter as tk
except ImportError:
    tk = None
import unittest
from unittest.mock import patch
if tk is not None:
    from drivebridge.app import App


@unittest.skipIf(tk is None, 'Tkinter não instalado neste servidor')
class ProgressLayoutTests(unittest.TestCase):
    def test_visible_footer_and_transfer_events(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
            try:
                root = tk.Tk()
            except tk.TclError:
                self.skipTest('Ambiente gráfico indisponível')
            try:
                app = App(root)
                root.geometry('780x600')
                root.update()
                self.assertTrue(app.progress.winfo_ismapped())
                self.assertLessEqual(app.progress.winfo_rooty() - root.winfo_rooty() + app.progress.winfo_height(), root.winfo_height())
                app.emit('phase', 'scanning')
                app.poll()
                root.update()
                self.assertEqual(str(app.progress.cget('mode')), 'indeterminate')
                self.assertIn('total ainda não calculado', app.details.get())
                now = time.monotonic()
                app.emit('phase', 'transferring')
                app.emit('progress', (0, 2048, '', 0, 1, now - 1, ''))
                app.emit('progress', (1024, 2048, 'teste.bin', 0, 1, now, 'download'))
                app.poll()
                root.update()
                self.assertEqual(app.progress['value'], 50)
                self.assertEqual(app.current_file.get(), 'Baixando: teste.bin')
                self.assertIn('/s', app.transfer_rate.get())
                app.emit('done', None)
                app.poll()
                self.assertEqual(str(app.progress.cget('mode')), 'determinate')
                self.assertFalse(app.transfer_stats.active)
            finally:
                root.destroy()
