TLS Validation Proxy
=====================

This is a simple proxy for TCP connections to check which ciphers a TLS client 
offers and validate those against a list of valid ciphers. This is to verify
that a client can not be tricked into weak encryption modes.

This proxy does *no* TLS termination, only looks at the client's hello package
and proxy all through. This means that the certificate the actual server uses
must be valid for the host the proxy is running on else clients will reject 
connecting. In the default way of using this tool, with MySQL Operator, this
is achieved by running it in the same Pod as the operator, which then is 
configured to connect to 127.0.0.1, which often is included in sever certificates.

By default connections using an invalid cipher are interrupted. This helps to
find misconfigured clients as they will error out with a TLS error. If 
connections should not be interrupted `--no-abort` can be passed a command line
option to the script.

Usage
-----

Primary usage is with MySQL Operator. A simple script is provided. Given a
default setup og MySQL Operator is running and `kubectl`with default context is
configured one can run

    ./activate.sh

or

    ./activate.sh --no-abort

From this directory. This will put the code in a ConfigMap and reconfigure
the MySQL Operator Deployment to run and use the proxy. Then the cpntainer's 
log can be observed.

When

    ./deactivate.sh

is executed the changes are reversed.

See code for some environment variables observed and for detaisl on behavior.
