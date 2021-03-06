from dexy.commands import InternalDexyProblem
from dexy.commands import UserFeedback
from dexy.filters.process_filters import DexyEOFException
from dexy.filters.process_filters import ProcessFilter
from ordereddict import OrderedDict
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.formatters.latex import LatexFormatter
from pygments.lexers import get_lexer_by_name
import codecs
import json
import os
import pexpect
import re
import shutil

class PexpectReplFilter(ProcessFilter):
    """
    Base class for filters which use pexpect to retrieve output line-by-line based on detecting prompts.
    """
    ALIASES = ['pexpectreplfilter']
    ALLOW_MATCH_PROMPT_WITHOUT_NEWLINE = False
    INITIAL_PROMPT = None
    INITIAL_PROMPT_TIMEOUT = 5
    TIMEOUT = 10 # Set to larger number if needed. Or set to None for no timeout.
    LINE_ENDING = "\n"
    PROMPTS = ['>>>', '...'] # Python uses >>> prompt normally and ... when in multi-line structures like loops
    PROMPT_REGEX = None
    PS1 = None
    PS2 = None
    PS3 = None
    PS4 = None
    SAVE_VARS_TO_JSON_CMD = None
    TRIM_PROMPT = '>>>'
    STRIP_REGEX = None

    def prompt_search_terms(self):
        """
        Search first for the prompt (or prompts) following a line ending.
        Also optionally allow matching the prompt with no preceding line ending.
        """

        if self.PROMPT_REGEX:
            prompts = [self.PROMPT_REGEX]
        elif hasattr(self, 'PROMPT'):
            prompts = [self.PROMPT]
        else:
            prompts = self.PROMPTS

        if self.ALLOW_MATCH_PROMPT_WITHOUT_NEWLINE:
            return ["%s%s" % (self.LINE_ENDING, p) for p in prompts] + prompts
        else:
            return ["%s%s" % (self.LINE_ENDING, p) for p in prompts]

    def lines_for_section(self, section_text):
        """
        Take the section text and split it into lines which will be sent to the
        interpreter. This can be overridden if lines need to be defined
        differently, or if you don't want the extra newline at the end.
        """
        return section_text.splitlines() + ["\n"]

    def strip_trailing_prompts(self, section_transcript):
        lines = section_transcript.splitlines()
        while len(lines) > 0 and re.match("^\s*(%s)\s*$|^\s*$" % self.TRIM_PROMPT, lines[-1]):
            lines = lines[0:-1]
        return self.LINE_ENDING.join(lines)

    def strip_newlines(self, line):
        return line.replace(" \r", "")

    def section_output(self, input_dict):
        """
        Runs the code in sections and returns an iterator so we can do custom stuff.
        """
        # If we want to automatically record values of local variables in the
        # script we are running, we add a section at the end of script
        do_record_vars = self.artifact.args.has_key('record-vars') and self.artifact.args['record-vars']
        if do_record_vars:
            if not self.SAVE_VARS_TO_JSON_CMD:
                raise UserFeedback("You specified record-vars but this option isn't available since SAVE_VARS_TO_JSON_CMD is not set for this filter.")
            artifact = self.artifact.add_additional_artifact(self.artifact.key + "-vars.json", 'json')
            self.log.debug("Added additional artifact %s (hashstring %s) to store variables" % (artifact.key, artifact.hashstring))
            section_text = self.SAVE_VARS_TO_JSON_CMD % artifact.filename()
            input_dict['dexy--save-vars'] = section_text
            # TODO this may cause unexpected results if original script only had 1 section

        search_terms = self.prompt_search_terms()

        env = self.setup_env()

        if self.args().has_key('PS1'):
            ps1 = self.arg_value('PS1')
            self.log.debug("Setting PS1 to %s" % ps1)
            env['PS1'] = ps1
        elif self.PS1:
            self.log.debug("Setting PS1 to %s" % self.PS1)
            env['PS1'] = self.PS1

        if self.args().has_key('PS2'):
            ps2 = self.arg_value('PS2')
            self.log.debug("Setting PS2 to %s" % ps2)
            env['PS2'] = ps2
        elif self.PS2:
            self.log.debug("Setting PS2 to %s" % self.PS2)
            env['PS2'] = self.PS2

        if self.args().has_key('PS3'):
            ps3 = self.arg_value('PS3')
            self.log.debug("Setting PS3 to %s" % ps3)
            env['PS3'] = ps3
        elif self.PS3:
            self.log.debug("Setting PS3 to %s" % self.PS3)
            env['PS3'] = self.PS3

        if self.args().has_key('PS4'):
            ps4 = self.arg_value('PS4')
            self.log.debug("Setting PS4 to %s" % ps4)
            env['PS4'] = ps4
        elif self.PS4:
            self.log.debug("Setting PS4 to %s" % self.PS4)
            env['PS4'] = self.PS4

        timeout = self.setup_timeout()
        initial_timeout = self.setup_initial_timeout()

        cwd=self.setup_cwd()
        self.log.debug("About to spawn new process '%s' in %s." % (self.executable(), cwd))

        # Spawn the process
        proc = pexpect.spawn(
            self.executable(),
            cwd=cwd,
            env=env)

        self.log.debug("Capturing initial prompt...")
        try:
            if self.INITIAL_PROMPT:
                proc.expect(self.INITIAL_PROMPT, timeout=initial_timeout)
            elif self.PROMPT_REGEX:
                proc.expect(search_terms, timeout=initial_timeout)
            else:
                proc.expect_exact(search_terms, timeout=initial_timeout)

        except pexpect.TIMEOUT:
            if self.INITIAL_PROMPT:
                match = self.INITIAL_PROMPT
            else:
                match = search_terms

            msg = "%s failed at matching initial prompt within %s seconds. " % (self.__class__.__name__, initial_timeout)
            msg += "Received '%s', tried to match with '%s'" % (proc.before, match)
            msg += "\nExact characters received:\n"
            for i, c in enumerate(proc.before):
                msg += "chr %02d: %s\n" % (i, ord(c))
            msg += "The developer might need to set a longer INITIAL_PROMPT_TIMEOUT or the regexp may be wrong."
            raise InternalDexyProblem(msg)

        start = proc.before + proc.after

        self.log.debug(u"Initial prompt captured!")
        self.log.debug(unicode(start))

        for section_key, section_text in input_dict.items():
            section_transcript = start
            start = ""

            lines = self.lines_for_section(section_text)
            for l in lines:
                self.log.debug(u"Sending '%s'" % l)
                section_transcript += start
                proc.send(l.rstrip() + "\n")
                try:
                    if self.PROMPT_REGEX:
                        proc.expect(search_terms, timeout=timeout)
                    else:
                        proc.expect_exact(search_terms, timeout=timeout)


                    self.log.debug(u"Received '%s'" % unicode(proc.before, errors='replace'))

                    section_transcript += self.strip_newlines(proc.before)
                    start = proc.after
                except pexpect.EOF:
                    self.log.debug("EOF occurred!")
                    if not self.ignore_errors():
                        raise DexyEOFException()
                except pexpect.TIMEOUT:
                    msg = "Failed at matching prompt within %s seconds. " % timeout
                    msg += "Received '%s', tried to match with '%s'" % (proc.before, search_terms)
                    msg += "Something may have gone wrong, or you may need to set a longer timeout."
                    raise UserFeedback(msg)
                except pexpect.ExceptionPexpect as e:
                    raise UserFeedback(str(e))
                except pexpect.EOF as e:
                    raise UserFeedback(str(e))

            if self.STRIP_REGEX:
                section_transcript = re.sub(self.STRIP_REGEX, "", section_transcript)

            yield section_key, section_transcript

        try:
            proc.close()
        except pexpect.ExceptionPexpect:
            raise UserFeedback("process %s may not have closed" % proc.pid)

        if proc.exitstatus and self.CHECK_RETURN_CODE:
            self.handle_subprocess_proc_return(self.executable(), proc.exitstatus, section_transcript)

    def process(self):
        lexer = None
        if self.arg_value('meta') and hasattr(self, 'OUTPUT_LEXER'):
            kv_artifact = self.artifact.add_additional_artifact("%s-meta.json" % self.artifact.key)
            html_formatter = HtmlFormatter()
            latex_formatter = LatexFormatter()
            lexer = get_lexer_by_name(self.OUTPUT_LEXER)
            kv_artifact.setup_kv_storage()

        self.log.debug("args: " % self.args())
        for section_key, section_transcript in self.section_output(self.artifact.input_data_dict):
            self.log.debug("Processing section %s" % section_key)
            output = self.strip_trailing_prompts(section_transcript)
            if self.arg_value('meta'):
                self.log.debug("in meta")
                # Use key-value storage to save file system and other information at each step of run.
                kv_artifact._storage.append("%s:output" % section_key, output)
                if lexer:
                    kv_artifact._storage.append("%s:html-output" % section_key, highlight(output, lexer, html_formatter))
                    kv_artifact._storage.append("%s:latex-output" % section_key, highlight(output, lexer, latex_formatter))

                for root, dirs, files in os.walk(self.artifact.temp_dir()):
                    for filename in files:
                        filepath = os.path.join(root, filename)
                        local_filepath = os.path.relpath(filepath, self.artifact.temp_dir())
                        key_name_for_file = "%s:files:%s" % (section_key, local_filepath)

                        try:
                            with codecs.open(filepath, "r") as f:
                                contents = f.read()

                            # make sure the contents are unicode-safe
                            json.dumps(contents)

                            kv_artifact._storage.append(key_name_for_file, contents)
                        except UnicodeDecodeError as e:
                            self.log.debug("Not appending %s because %s" % (key_name_for_file, e.message))
                        except Exception as e:
                            self.log.debug("error message: %s" % e)
                            raise InternalDexyProblem("%s reading file contents of %s or appending to db" % (e.__class__.__name__, filepath))
            self.artifact.data_dict[section_key] = output

        if self.arg_value('meta'):
            kv_artifact._storage.save()

        self.copy_additional_inputs()

class RubyPexpectReplFilter(PexpectReplFilter):
    ALIASES = ['irb', 'rbrepl']
    EXECUTABLE = 'irb --simple-prompt'
    CHECK_RETURN_CODE = False
    INITIAL_PROMPT = "^>>"
    INPUT_EXTENSIONS = [".txt", ".rb"]
    OUTPUT_EXTENSIONS = [".rbcon"]
    OUTPUT_LEXER = "irb"
    PROMPTS = [">>", "?>"]
    TRIM_PROMPT = '>>'
    VERSION_COMMAND = 'irb --version'

class PythonPexpectReplFilter(PexpectReplFilter):
    ALIASES = ['pycon', 'pyrepl']
    CHECK_RETURN_CODE = False
    EXECUTABLE = 'python'
    INPUT_EXTENSIONS = [".txt", ".py"]
    OUTPUT_EXTENSIONS = [".pycon"]
    OUTPUT_LEXER = "pycon"
    TAGS = ['python', 'interpreter', 'language']
    VERSION_COMMAND = 'python --version'
    INITIAL_PROMPT = ">>>"

    SAVE_VARS_TO_JSON_CMD = """
import json
dexy__vars_file = open("%s", "w")
dexy__x = {}
for dexy__k, dexy__v in locals().items():
    dexy__x[dexy__k] = str(dexy__v)

json.dump(dexy__x, dexy__vars_file)
dexy__vars_file.close()
"""

class IpythonPexpectReplFilter(PythonPexpectReplFilter):
    # TODO test for version of ipython that supports --classic
    ALIASES = ['ipython']
    EXECUTABLE = 'ipython --classic'
    CHECK_RETURN_CODE = False # TODO try to figure out why we are getting nonzero exit codes
    INPUT_EXTENSIONS = [".txt", ".py"]
    OUTPUT_EXTENSIONS = [".pycon"]
    VERSION_COMMAND = 'ipython -Version'

class RPexpectReplFilter(PexpectReplFilter):
    ALIASES = ['r', 'rint']
    CHECK_RETURN_CODE = False
    EXECUTABLE = "R --quiet --vanilla"
    INPUT_EXTENSIONS = ['.txt', '.r', '.R']
    OUTPUT_EXTENSIONS = ['.Rout']
    PROMPT_REGEX = "(\x1b[^m]*m)?(>|\+)\s*"
    INITIAL_PROMPT = "(\x1b[^>])?>\s*"
    TAGS = ['r', 'interpreter', 'language']
    TRIM_PROMPT = ">"
    STRIP_REGEX = "(\x1b[^h]+h)" # Strip weird initial prompt on OSX
    VERSION_COMMAND = "R --version"
    SAVE_VARS_TO_JSON_CMD = """
if ("rjson" %%in%% installed.packages()) {
    library(rjson)
    dexy__json_file <- file("%s", "w")
    writeLines(toJSON(as.list(environment())), dexy__json_file)
    close(dexy__json_file)
} else {
   cat("Can't automatically save environment to JSON since rjson package not installed.")
}
"""

class RhinoInteractiveFilter(PexpectReplFilter):
    """
    Runs rhino JavaScript interpeter.
    """
    EXECUTABLE = "rhino"
    INPUT_EXTENSIONS = [".js", ".txt"]
    OUTPUT_EXTENSIONS = [".jscon"]
    ALIASES = ['jsint', 'rhinoint']
    PROMPTS = ['js>', '  >']
    TRIM_PROMPT = "js>"
    INITIAL_PROMPT_TIMEOUT = 60

class PhpInteractiveFilter(PexpectReplFilter):
    """
    Runs PHP in interpeter mode.
    """
    CHECK_RETURN_CODE = False
    EXECUTABLE = "php -a"
    INPUT_EXTENSIONS = [".php", ".txt"]
    OUTPUT_EXTENSIONS = [".txt"]
    ALIASES = ['phpint']
    PROMPTS = ['php > ']
    TRIM_PROMPT = "php > "

class BashInteractiveFilter(PexpectReplFilter):
    """
    Runs bash. Use to run bash scripts.
    """
    ALIASES = ['shint', 'bashint']
    EXECUTABLE = "bash --norc -i"
    INPUT_EXTENSIONS = [".txt", ".sh"]
    OUTPUT_EXTENSIONS = ['.sh-session']
    OUTPUT_LEXER = "console"
    PROMPT_REGEX = r"\d*[#$]"
    INITIAL_PROMPT = PROMPT_REGEX
    TRIM_PROMPT = PROMPT_REGEX
    PS1 = "$ "

class KshInteractiveFilter(PexpectReplFilter):
    """
    Runs ksh. Use to run bash scripts.
    """
    ALIASES = ['kshint']
    EXECUTABLE = "ksh -i"
    INPUT_EXTENSIONS = [".txt", ".sh"]
    OUTPUT_EXTENSIONS = ['.sh-session']
    INITIAL_PROMPT = "^\s*\d*(#|\$)\s+"
    PROMPT_REGEX = "\d*(#|\$)"
    TRIM_PROMPT = r"\d*(\$|#)"
    PS1 = "$ "

class ClojureInteractiveFilter(PexpectReplFilter):
    """
    Runs clojure.
    """
    ALIASES = ['clj', 'cljint']
    CHECK_RETURN_CODE = False
    EXECUTABLES = ['clojure -r', 'clj -r']
    INITIAL_PROMPT_TIMEOUT = 15
    INPUT_EXTENSIONS = [".clj", ".txt"]
    OUTPUT_EXTENSIONS = [".txt"]
    PROMPT = "user=> "

    def lines_for_section(self, input_text):
        input_lines = []
        current_line = []
        in_indented_block = False
        for l in input_text.splitlines():
            if re.match("^\s+", l):
                in_indented_block = True
                current_line.append(l)
            else:
                if len(current_line) > 0:
                    input_lines.append("\n".join(current_line))
                if in_indented_block:
                    # we have reached the end of this indented block
                    in_indented_block = False
                current_line = [l]
        input_lines.append("\n".join(current_line))
        return input_lines
