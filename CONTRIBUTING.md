# Contributing Guidelines

We welcome your code contributions. Before submitting code via a GitHub pull 
request, or by filing a bug in https://bugs.mysql.com you will need to have 
signed the Oracle Contributor Agreement (see https://oca.opensource.oracle.com).
Only pull requests from committers that can be verified as having signed the OCA
can be accepted.

## Contributing Code

1. Make sure you have a user account at bugs.mysql.com. You'll need to reference this 
    user account when you submit your OCA (Oracle Contributor Agreement).
2. Sign the Oracle OCA. You can find instructions for doing that at the OCA Page,
    at https://oca.opensource.oracle.com
3. Validate your contribution by including tests that sufficiently cover the functionality.
4. Verify that the entire test suite passes with your code applied.
5. Submit your pull request via GitHub or uploading it using the contribution tab to a bug 
    record in https://bugs.mysql.com (using the 'contribution' tab).

## Non-Code Contributions

Submissions Other than Code. These terms apply to all of Your Submissions other than
code contributions. "You" means you personally, as well as any person or entity on
whose behalf you are Using the Site. "You" does not include Oracle or its employees
using the Site on Oracle's behalf. "Use" and its variants are to be interpreted in
their broadest sense and include, without limitation, the acts of using, accessing,
receiving, browsing, downloading from, and uploading to. A "User" is a person or
entity who Uses the site.
"Submissions" means any materials (other than code contributions), including but not
limited to technology specifications, technical materials, documentation, discussion
thread postings, blogs, wikis, data, and any other content, information, technology
or services submitted to by You to the site.
You hereby grant to Oracle and all Users a royalty-free, perpetual, irrevocable,
worldwide, non-exclusive and fully sub-licensable right and license under Your
intellectual property rights to reproduce, modify, adapt, publish, translate, create
derivative works from, distribute, perform, display and use Your Submissions (in whole
or part) and to incorporate or implement them in other works in any form, media, or
technology now known or later developed. This includes, without limitation, the right
to incorporate or implement the Submission into any product or service, and to display,
market, sublicense and distribute the Submissions as incorporated or embedded in any
product or service distributed or offered by Oracle without compensation to you.
All Users, Oracle, and their sublicensees are responsible for any modifications they
make to the Submissions of others.

## Reporting Issues

Before reporting a new bug, please [check](https://bugs.mysql.com/search.php?bug_type[]=Server%3A+Shell+OPR) if a similar bug already exists.

Please report detailed bugs by including the following:

* Complete steps to reproduce the issue.
* Relevant platform and environment information potentially specific to the bug.
* Specific version of the relevant products you are using.
* Specific version of the server being used.
* Sample code to help reproduce the issue, if possible.

## Setting Up a Development Environment

The following tips provide technical details to follow when writing code for the contribution.

### Building a test image

Building container images for the MySQL Operator with our provided `Dockerfile` and build scripts can be a bit tedious, as they are tied to our development environment.

For changes only to the Operator code, which don't require changes to dependencies, an easy alternative is just to patch the images we provide adding your code changes. A way to facilitate is via a `Dockerfile` like this:

    ARG BASE_VERSION=9.7.0-2.2.8
    FROM container-registry.oracle.com/mysql/community-operator:$BASE_VERSION
    COPY mysqloperator/ /usr/lib/mysqlsh/python-packages/

After building an image like this:

    docker build -t  mysql/community-operator:9.7.0-2.2.8 -f Dockerfile.patch .

This can be passed to a local registry and used from there. Please refer to the MySQL Operator documentation and the documentation of your Kubernetes distribution of choice.

Note: the operator has specific naming expectations to deploy the correct image as a sidecar to the server pods and for backup purposes. After deploying a cluster, verify that the expected image was used.

Different Kubernetes distributions also provide the ability to mount local directories into a Kubernetes Node and from there into a pod. Doing this means avoiding the frequent rebuilding of images. For example, when using using k3d:

    k3d  cluster create SOME_NAME --volume /full/path/to/the/git/checkout:/src

This makes the directory available on the k3d node. By editing `deploy/deploy-operator.yaml` that source can then be used. An easy way to patch this is by using the the provided script.

    deploy/patch_deployment.py --prefix registry.localhost:5000 --pull-policy Always --debug --src-volume /src/mysqloperator  deploy/deploy-operator.yaml

When deployed this way to test code changes, you can test the code by simply restarting the Operator pod. The easiest way to do that is by using:

    kubectl rollout restart deployment -n mysql-operator mysql-operator

Note: This patched code will only be seen on the operator itself. Changes won't affect sidecar or backup usage.

### Executing the Test Suite

The test suite is composed of two different categories of automated tests:

* Unit tests
* Functional tests

For details on running the test suite please refer to the `README.md` file in the `tests/` directory.

## Getting Help

If you need help or want to contact us, please use the following resources:

* [MySQL Operator for Kubernetes Documentation](https://dev.mysql.com/doc/mysql-operator/en/)
* [`#mysql-operator` channel in MySQL Community Slack](https://mysqlcommunity.slack.com/messages/mysql-operator) ([Sign-up](https://lefred.be/mysql-community-on-slack/) required if you do not have an Oracle account)
* [MySQL Container and Kubernetes forum](http://forums.mysql.com/list.php?149)

