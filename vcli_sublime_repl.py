import logging
import sublime
from .vcli_sublime import format_results
from time import sleep

try:
    from SublimeREPL.repls import Repl
except ImportError:
    Repl = object

logger = logging.getLogger('vcli_sublime.repl')


class SublimeVcliRepl(Repl):
    TYPE = "vcli"

    def __init__(self, encoding, vcli_url=None):
        super(SublimeVcliRepl, self).__init__(encoding,
                                               additional_scopes=['sql'])

        global vertica_python
        from .vcli_sublime import VCli, vertica_python
        settings = sublime.load_settings('VcliSublime.sublime_settings')
        vclirc = settings.get('vclirc')

        logger.debug('Vcli url: %r', vcli_url)
        self.url = vcli_url
        self.vcli = VCli(vclirc_file=vclirc)
        self.vcli.connect_uri(vcli_url)
        self.vcli.refresh_completions()
        self._query = None
        self._brand_new = True

    def name(self):
        return 'vcli'

    def write(self, sql):
        logger.debug('Write: %r', sql)
        self._query = sql

    def prompt(self):
        return '{}> '.format(self.vcli.vexecute.dbname)

    def read(self):

        # Show the initial prompt
        if self._brand_new:
            logger.debug('Brand new prompt')
            self._brand_new = False
            return self.prompt()

        # Block until a command is entered
        while not self._query:
            sleep(.1)

        logger.debug('Query: %r', self._query)

        try:
            results = self.vcli.vexecute.run(self._query)
            results = format_results(results, self.vcli.table_format)
        except vertica_python.errors.Error as e:
            results = e.verror
        finally:
            self._query = None

        if results:
            return '\n' + results + '\n\n' + self.prompt()
        else:
            return self.prompt()

    def autocomplete_completions(self, whole_line, pos_in_line, *args, **kwargs):
        comps = self.vcli.get_completions(whole_line, pos_in_line)
        return [(comp.text, comp.display) for comp in comps]

    def is_alive(self):
        return self.vcli is not None

    def kill(self):
        self.vcli = None

    def allow_restarts(self):
        return True

    def autocomplete_available(self):
        return True
