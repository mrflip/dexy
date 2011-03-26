try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

from dexy.handler import DexyHandler
from pygments import highlight
from pygments.formatters import get_formatter_for_filename
from pygments.lexers import get_lexer_for_filename
from pygments.lexers.agile import PythonConsoleLexer
from pygments.lexers.agile import RubyConsoleLexer
from pygments.lexers.web import JavascriptLexer

class PygHandler(DexyHandler):
    """
    Apply Pygments syntax highlighting.
    """
    INPUT_EXTENSIONS = [".*"]
    OUTPUT_EXTENSIONS = [".html", ".tex"]
    ALIASES = ['pyg', 'pygments']

    def process_dict(self, input_dict):
        name = "input_text%s" % self.ext
        # List any file extensions which don't map neatly to lexers.
        if self.ext == '.pycon':
            lexer = PythonConsoleLexer()
        elif self.ext == '.rbcon':
            lexer = RubyConsoleLexer()
        elif self.ext in ('.json', '.dexy'):
            lexer = JavascriptLexer()
        else:
            lexer = get_lexer_for_filename(name)
        formatter = get_formatter_for_filename(self.artifact.filename(), linenos=False)
        output_dict = OrderedDict()
        for k, v in input_dict.items():
            try:
                output_dict[k] = str(highlight(v, lexer, formatter))
            except UnicodeEncodeError as e:
                self.log.warn("error processing section %s of file %s" % (k, self.artifact.key))
                raise e
        return output_dict
