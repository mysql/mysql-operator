import sys

if len(sys.argv) != 2:
    raise Exception("{0} requires two parameters".format(sys.argv[0]))

input_cmd = ""
output_cmd = {
    "": "\nprint('empty input file')\n",
    "first": "\nprint('first test')\n",
    "second": "\nprint('second test')\n",
    "multiline": "def multiline(req, opt = False):\n"
                 "  if opt:\n"
                 "    print('multiline -> optional')\n"
                 "  \n"
                 "  print('multiline test: {0}'.format(req))\n",
    "temporary": "\ntemporary_file_path = r'{0}'\n".format(sys.argv[1]),
    "extra \"quoted \\\"arguments\\\"\"": "\nprint('test with extra additional quoted arguments')\n",
    "display": "def display():\n"
               "  print('display test')\n",
    "js_statements": "js_one = 'first JS'\n"
                     "js_two = 'second JS'\n",
    "sql_statements": "SELECT 'first SQL';\n"
                      "SELECT 'second SQL';\n",
    "py_statements": "py_one = 'first Python'\n"
                     "py_two = 'second Python'\n",
    "print('this')": "\nprint('that')\n"
}

with open(sys.argv[1], "r") as f:
    input_cmd = f.read()

if not input_cmd in output_cmd:
    output_cmd[input_cmd] = "print('unexpected test: \\'{0}\\'')\n".format(input_cmd)

with open(sys.argv[1], "w") as f:
    f.write(output_cmd[input_cmd])
