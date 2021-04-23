import sys

if len(sys.argv) < 2:
    raise Exception("{0} requires at least one parameter".format(sys.argv[0]))

with open(sys.argv[1], "w") as f:
    output = str(sys.argv[2:])
    f.write(output)
    print(output)
