# SPDX-License-Identifier: CC0-1.0
# Based on jktr/matplotlib-backend-kitty.
# Key changes vs upstream:
#   1. Use `kitten icat` (not `kitty +kitten icat`) — works in tmux with
#      `allow-passthrough on` because kitten negotiates the passthrough itself.
#   2. Add `preserve_aspect_ratio` sizing strategy (new default): fits the
#      figure inside the terminal while keeping the original aspect ratio,
#      instead of always stretching to fill the window.
#   3. Register a `post_execute` IPython hook (`flush_figures`) so that
#      figures are displayed automatically at the end of every IPython cell,
#      matching the behaviour of the inline backend.

import os
import sys

from io import BytesIO
from subprocess import run

import matplotlib as mpl
from matplotlib import interactive, is_interactive
from matplotlib._pylab_helpers import Gcf
from matplotlib.backend_bases import (_Backend, FigureManagerBase)
from matplotlib.backends.backend_agg import FigureCanvasAgg

try:
    from IPython import get_ipython
except ModuleNotFoundError:
    def get_ipython():
        return None


# XXX heuristic for interactive repl
if hasattr(sys, 'ps1') or sys.flags.interactive:
    interactive(True)


class FigureManagerICat(FigureManagerBase):

    @classmethod
    def _run(cls, *cmd):
        def f(*args, output=True, **kwargs):
            if output:
                kwargs['capture_output'] = True
                kwargs['text'] = True
            r = run(cmd + args, **kwargs)
            if output:
                return r.stdout.rstrip()
        return f

    def show(self):
        icat = __class__._run('kitten', 'icat')

        sizing_strategy = os.environ.get('MPLBACKEND_KITTY_SIZING', 'preserve_aspect_ratio')
        if sizing_strategy in ['automatic', 'preserve_aspect_ratio']:
            # gather terminal dimensions via kitten icat --print-window-size
            px_str = icat('--print-window-size')
            try:
                px_w, px_h = map(int, px_str.split('x'))
            except (ValueError, AttributeError):
                px_w, px_h = 0, 0

            if px_w > 0 and px_h > 0:
                # account for post-display prompt scrolling (3 lines)
                try:
                    from shutil import get_terminal_size
                    rows = get_terminal_size().lines
                except Exception:
                    rows = 24
                px_h -= int(3 * (px_h / rows))

                dpi = self.canvas.figure.dpi
                term_w_inch = px_w / dpi
                term_h_inch = px_h / dpi

                if sizing_strategy == 'automatic':
                    self.canvas.figure.set_size_inches(term_w_inch, term_h_inch)
                else:
                    # preserve_aspect_ratio: fit within terminal, keep aspect
                    fig_w, fig_h = self.canvas.figure.get_size_inches()
                    new_w = term_w_inch
                    new_h = new_w * fig_h / fig_w
                    if new_h > term_h_inch:
                        new_h = term_h_inch
                        new_w = new_h * fig_w / fig_h
                    self.canvas.figure.set_size_inches(new_w, new_h)

        with BytesIO() as buf:
            self.canvas.figure.savefig(buf, format='png')
            icat('--align', 'left', output=False, input=buf.getbuffer())


class FigureCanvasICat(FigureCanvasAgg):
    manager_class = FigureManagerICat


@_Backend.export
class _BackendICatAgg(_Backend):

    FigureCanvas = FigureCanvasICat
    FigureManager = FigureManagerICat

    # Noop function instead of None signals that
    # this is an "interactive" backend
    mainloop = lambda: None

    _to_show = []
    _draw_called = False

    # XXX: `draw_if_interactive` isn't really intended for
    # on-shot rendering. We run the risk of being called
    # on a figure that isn't completely rendered yet, so
    # we skip draw calls for figures that we detect as
    # not being fully initialized yet. Our heuristic for
    # that is the presence of axes on the figure.
    @classmethod
    def draw_if_interactive(cls):
        manager = Gcf.get_active()
        if is_interactive() and manager.canvas.figure.get_axes():
            cls.show()

    @classmethod
    def show(cls, *args, **kwargs):
        _Backend.show(*args, **kwargs)
        Gcf.destroy_all()

    @staticmethod
    def new_figure_manager_given_figure(num, figure):
        canvas = FigureCanvasICat(figure)
        manager = FigureManagerICat(canvas, num)
        if is_interactive():
            _BackendICatAgg._to_show.append(figure)
            figure.canvas.draw_idle()

        def destroy(event):
            canvas.mpl_disconnect(cid)

        cid = canvas.mpl_connect('close_event', destroy)

        if is_interactive():
            try:
                _BackendICatAgg._to_show.remove(figure)
            except ValueError:
                pass
            _BackendICatAgg._to_show.append(figure)
            _BackendICatAgg._draw_called = True

        return manager


def flush_figures():
    """IPython post_execute hook: display all pending figures automatically."""
    backend = mpl.get_backend()
    if backend == 'module://matplotlib-backend-kitty':
        if not _BackendICatAgg._draw_called:
            return

        try:
            active = {fm.canvas.figure for fm in Gcf.get_all_fig_managers()}
            for fig in [fig for fig in _BackendICatAgg._to_show if fig in active]:
                fig.show()
        finally:
            _BackendICatAgg._to_show = []
            _BackendICatAgg._draw_called = False


ip = get_ipython()
if ip is not None:
    ip.events.register('post_execute', flush_figures)
