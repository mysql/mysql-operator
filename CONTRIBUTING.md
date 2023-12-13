# Contributing Guidelines

We love getting feedback from our users. Bugs and code contributions are great forms of feedback and we thank you for any bugs you report or code you contribute.

## Reporting Issues

Before reporting a new bug, please [check](https://bugs.mysql.com/search.php?bug_type[]=Server%3A+Shell+OPR) if a similar bug already exists.

Please report detailed bugs by including the following:

* Complete steps to reproduce the issue.
* Relevant platform and environment information potentially specific to the bug.
* Specific version of the relevant products you are using.
* Specific version of the server being used.
* Sample code to help reproduce the issue, if possible.

## Contributing Code

Contributing to MySQL projects is easy by following these steps:

* Make sure you have a user account at [bugs.mysql.com](https://bugs.mysql.com). This account is referenced when submitting an Oracle Contributor Agreement (OCA).
* Sign the Oracle Contributor Agreement. You can find instructions for doing that at the [OCA Page](https://oca.opensource.oracle.com/).
* Develop your pull request. Make sure you are aware of the requirements for the project (e.g. do not require Kubernetes 1.10.x if we are supporting Kubernetes 1.12.x and higher).
* Ideally validate your pull request by including tests that sufficiently cover the functionality you are adding.
* Verify that the entire test suite passes with your code applied.
* Submit your pull request. While you can submit the pull request via [GitHub](https://github.com/mysql/mysql-operator/pulls), you can also submit it directly via [bugs.mysql.com](https://bugs.mysql.com).

Thanks again for your wish to contribute to MySQL. We truly believe in the principles of open source development and appreciate any contributions to our projects.

## Setting Up a Development Environment

The following tips provide technical details to follow when writing code for the contribution.

### Building a test image

Building container images for the MySQL Operator with our provided `Dockerfile` and build scripts can be a bit tedious, as they are tied to our development environment.

For changes only to the Operator code, which don't require changes to dependencies, an easy alternative is just to patch the images we provide adding your code changes. A way to facilitate is via a `Dockerfile` like this:

    ARG BASE_VERSION=8.4.0-2.1.3
    FROM container-registry.oracle.com/mysql/community-operator:$BASE_VERSION
    COPY mysqloperator/ /usr/lib/mysqlsh/python-packages/

After building an image like this:

    docker build -t  mysql/community-operator:8.4.0-2.1.3 -f Dockerfile.patch .

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

