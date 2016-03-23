import sublime
import sublime_plugin
import logging
import sys
import os
import site
import traceback
import queue
import datetime
from urllib.parse import urlparse
from threading import Lock, Thread

try:
    from SublimeREPL.repls import Repl
    SUBLIME_REPL_AVAIL = True
except ImportError:
    SUBLIME_REPL_AVAIL = False

completers = {}  # Dict mapping urls to vcompleter objects
completer_lock = Lock()

executors = {}  # Dict mapping buffer ids to vexecutor objects
executor_lock = Lock()

recent_urls = []


logger = logging.getLogger('vcli_sublime')


def plugin_loaded():
    global settings
    settings = sublime.load_settings('VcliSublime.sublime_settings')

    init_logging()
    logger.debug('Plugin loaded')

    # Before we can import vcli, we need to know its path. We can't know that
    # until we load settings, and we can't load settings until plugin_loaded is
    # called, which is why we need to import to a global variable here

    sys.path = settings.get('vcli_dirs') + sys.path
    for sdir in settings.get('vcli_site_dirs'):
        site.addsitedir(sdir)

    logger.debug('System path: %r', sys.path)

    global VCli, need_completion_refresh, need_search_path_refresh
    from vcli.main import VCli, need_completion_refresh, need_search_path_refresh

    global VExecute
    from vcli.vexecute import VExecute

    global VCompleter
    from vcli.vcompleter import VCompleter

    global special
    from vcli.packages.vspecial import VSpecial
    special = VSpecial()

    global CompletionRefresher
    from vcli.completion_refresher import CompletionRefresher

    global Document
    from prompt_toolkit.document import Document

    global format_output
    from vcli.main import format_output

    global vertica_python
    import vertica_python

    global sqlparse
    import sqlparse


def plugin_unloaded():
    global MONITOR_URL_REQUESTS
    MONITOR_URL_REQUESTS = False

    global vclis
    vclis = {}

    global url_requests
    url_requests = queue.Queue()


class VcliPlugin(sublime_plugin.EventListener):
    def on_post_save_async(self, view):
        check_vcli(view)

    def on_load_async(self, view):
        check_vcli(view)

    def on_activated(self, view):
        # This should be on_activated_async, but that's not called correctly
        # on startup for some reason
        sublime.set_timeout_async(lambda: check_vcli(view), 0)

    def on_query_completions(self, view, prefix, locations):

        if not get(view, 'vcli_autocomplete'):
            return []

        logger.debug('Searching for completions')

        url = get(view, 'vcli_url')
        if not url:
            return

        with completer_lock:
            completer = completers.get(url)

        if not completer:
            return

        text = get_entire_view_text(view)
        cursor_pos = view.sel()[0].begin()
        logger.debug('Position: %d Text: %r', cursor_pos, text)

        comps = completer.get_completions(
            Document(text=text, cursor_position=cursor_pos), None)

        if not comps:
            logger.debug('No completions found')
            return []

        comps = [('{}\t{}'.format(c.text, c.display_meta), c.display)
                    for c in comps]
        logger.debug('Found completions: %r', comps)

        return comps, (sublime.INHIBIT_WORD_COMPLETIONS
                        | sublime.INHIBIT_EXPLICIT_COMPLETIONS)


class VcliSwitchConnectionStringCommand(sublime_plugin.TextCommand):
    def description(self):
        return 'Change the current connection string'

    def run(self, edit):

        recent = set(recent_urls)
        extra = get(self.view, 'vcli_urls')
        urls = list(reversed(recent_urls)) + [
            u for u in extra if u not in recent]

        def callback(i):
            if i == -1:
                return
            self.view.settings().set('vcli_url', urls[i])
            del self.view.vcli_executor
            check_vcli(self.view)

        self.view.window().show_quick_panel(urls, callback)


class VcliRunAllCommand(sublime_plugin.TextCommand):
    def description(self):
        return 'Run the entire contents of the view as a query'

    def run(self, edit):
        logger.debug('VcliRunAllCommand')
        check_vcli(self.view)
        sql = get_entire_view_text(self.view)
        t = Thread(target=run_sql_async,
                   args=(self.view, sql),
                   name='run_sql_async')
        t.setDaemon(True)
        t.start()


class VcliRunCurrentCommand(sublime_plugin.TextCommand):
    def description(self):
        return 'Run the current selection or line as a query'

    def run(self, edit):
        logger.debug('VcliRunCurrentCommand')
        check_vcli(self.view)

        # Note that there can be multiple selections
        sel = self.view.sel()
        contents = [self.view.substr(reg) for reg in sel]
        sql = '\n'.join(contents)

        if not sql and len(sel) == 1:
            # Nothing highlighted - find the current query
            sql = get_entire_view_text(self.view)
            split_sql = sqlparse.split(sql)
            curr_point = sel[0].a
            cum_len = 0

            for sql in split_sql:
                cum_len += len(sql)
                if curr_point <= cum_len:
                    break

        # Run the sql in a separate thread
        t = Thread(target=run_sql_async,
                   args=(self.view, sql),
                   name='run_sql_async')
        t.setDaemon(True)
        t.start()

class VcliShowOutputPanelCommand(sublime_plugin.TextCommand):
    def description(self):
        return 'Show the output panel'

    def run(self, edit):
        logger.debug('VcliShowOutputPanelCommand')
        sublime.active_window().run_command('show_panel',
                {'panel': 'output.' + output_panel_name(self.view)})


class VcliOpenCliCommand(sublime_plugin.TextCommand):
    def description(self):
        return 'Open a vcli command line prompt'

    def run(self, edit):
        logger.debug('VcliOpenCliCommand')

        url = get(self.view, 'vcli_url')
        if not url:
            logger.debug('No url for current view')
            return

        logger.debug('Opening a command prompt for url: %r', url)
        cmd = get(self.view, 'vcli_system_cmd')
        cmd = cmd.format(url=url)
        os.system(cmd)


class VcliNewSqlFileCommand(sublime_plugin.WindowCommand):
    def description(self):
        return 'Open a new SQL file'

    def run(self):
        """Open a new file with syntax defaulted to SQL"""
        logger.debug('VcliNewSqlFile')
        self.window.run_command('new_file')
        view = self.window.active_view()
        view.set_syntax_file('Packages/SQL/SQL.tmLanguage')
        view.set_scratch(True)
        sublime.set_timeout_async(lambda: check_vcli(view), 0)


class VcliNewSublimeReplCommand(sublime_plugin.WindowCommand):
    def description(self):
        return 'Open a new vcli REPL in SublimeREPL'

    def run(self):
        logger.debug('VcliNewSublimeRepl')
        if self.window.active_view():
            url = get(self.window.active_view(), 'vcli_url')
        else:
            url = settings.get('vcli_url')

        self.window.run_command('repl_open',
              {'encoding': 'utf8',
               'type': 'vcli',
               'syntax': 'Packages/SQL/SQL.tmLanguage',
               'vcli_url': url})

    def is_enabled(self):
        return SUBLIME_REPL_AVAIL

    def is_visible(self):
        return SUBLIME_REPL_AVAIL


class VcliSetScratchCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.active_view().set_scratch(True)


def init_logging():

    for h in logger.handlers:
        logger.removeHandler(h)

    logger.setLevel(settings.get('vcli_log_level', 'WARNING'))

    h = logging.StreamHandler(sys.stdout)
    h.setLevel(settings.get('vcli_console_log_level', 'WARNING'))
    fmt = logging.Formatter('%(name)s: %(levelname)s: %(message)s')
    h.setFormatter(fmt)
    logger.addHandler(h)

    vcli_logger = logging.getLogger('vcli')
    vcli_logger.addHandler(h)


def is_sql(view):
    if view.settings().get('repl'):
        # vcli sublime repl has it's own thing
        return False

    syntax_file = view.settings().get('syntax')
    return 'sql' in syntax_file.lower()


def check_vcli(view):
    """Check if a vcli connection for the view exists, or request one"""

    if not is_sql(view):
        view.set_status('vcli', '')
        return

    with executor_lock:
        buffer_id = view.buffer_id()
        if buffer_id not in executors:
            url = get(view, 'vcli_url')

            if not url:
                view.set_status('vcli', '')
                logger.debug('Empty vcli url %r', url)
            else:
                # Make a new executor connection
                view.set_status('vcli', 'Connecting: ' + url)
                logger.debug('Connecting to %r', url)

                try:
                    executor = new_executor(url)
                    view.set_status('vcli', vcli_id(executor))
                except Exception as e:
                    logger.error('Error connecting to vcli')
                    logger.error('traceback: %s', traceback.format_exc())
                    executor = None
                    status = 'ERROR CONNECTING TO {}'.format(url)
                    view.set_status('vcli', status)

                executors[buffer_id] = executor

                # Make sure we have a completer for the corresponding url
                with completer_lock:
                    need_new_completer = executor and url not in completers
                    if need_new_completer:
                        completers[url] = VCompleter()  # Empty placeholder

                if need_new_completer:
                    refresher = CompletionRefresher()
                    refresher.refresh(executor, special=special, callbacks=(
                        lambda c: swap_completer(c, url)))


def swap_completer(new_completer, url):
    with completer_lock:
        completers[url] = new_completer


def get(view, key):
    # Views may belong to projects which have project specific overrides
    # This method returns view settings, and falls back to base plugin settings
    val = view.settings().get(key)
    return val if val else settings.get(key)


def get_entire_view_text(view):
    return view.substr(sublime.Region(0, view.size()))


def vcli_id(executor):
    user, host, db = executor.user, executor.host, executor.dbname
    return '{}@{}/{}'.format(user, host, db)


def output_panel_name(view):
    return '__vcli__' + (view.file_name() or 'untitled')


def get_output_panel(view):
    return view.window().create_output_panel(output_panel_name(view))


def format_results(results, table_format):
    out = []

    for title, cur, headers, status, _ in results:
        fmt = format_output(title, cur, headers, status, table_format)
        out.append('\n'.join(fmt))

    return '\n\n'.join(out)


def new_executor(url):
    uri = urlparse(url)
    database = uri.path[1:]  # ignore the leading fwd slash
    return VExecute(database, uri.username, uri.password, uri.hostname,
                     uri.port)


def run_sql_async(view, sql):
    executor = executors[view.buffer_id()]
    panel = get_output_panel(view)
    logger.debug('Command: VcliExecute: %r', sql)
    save_mode = get(view, 'vcli_save_on_run_query_mode')

    # Make sure the output panel is visiblle
    sublime.active_window().run_command('vcli_show_output_panel')

    # Put a leading datetime
    datestr = str(datetime.datetime.now()) + '\n\n'
    panel.run_command('append', {'characters': datestr, 'pos': 0})
    results = executor.run(sql, vspecial=special)

    try:
        for (title, cur, headers, status, _) in results:
            fmt = format_output(title, cur, headers, status, 'vsql')
            out = ('\n'.join(fmt)
                   + '\n\n' + str(datetime.datetime.now()) + '\n\n')
            panel.run_command('append', {'characters': out})
    except vertica_python.errors.DatabaseError as e:
        success = False
        out = str(e) + '\n\n' + str(datetime.datetime.now()) + '\n\n'
        panel.run_command('append', {'characters': out})
    else:
        success = True

    if (view.file_name()
            and ((save_mode == 'always')
                 or (save_mode == 'success' and success))):
        view.run_command('save')


    # Refresh the table names and column names if necessary.
    if need_completion_refresh(sql):
        logger.debug('Need completions refresh')
        url = get(view, 'vcli_url')
        refresher = CompletionRefresher()
        refresher.refresh(executor, special=special, callbacks=(
                          lambda c: swap_completer(c, url)))

    # Refresh search_path to set default schema.
    if need_search_path_refresh(sql):
        logger.debug('Refreshing search path')
        url = get(view, 'vcli_url')

        with completer_lock:
            completers[url].set_search_path(executor.search_path())
            logger.debug('Search path: %r', completers[url].search_path)

