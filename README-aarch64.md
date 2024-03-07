# Modified MySQL Operator for Kubernetes

## Introduction

This repository introduces some changes to the original operator.
The changes are needed if we want the operator (and its resources) to run on ARM64 processors.
To that end, the following files were modified:

| File                | Change Description                    |
| :-                  | :-                                    |
| [`.gitignore`](https://github.com/IFeelFine/mysql-operator/blob/aarch64/.gitignore)          | Add .DS_Store to ignore macOS filesystem elements. |
| [`Dockerfile.patch`](https://github.com/IFeelFine/mysql-operator/blob/aarch64/Dockerfile.patch)    | Created this file to build our custom operator as suggested in [CONTRIBUTING.md](/CONTRIBUTING.md). |
| [`mysqloperator/controller/config.py`](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py) | Wherever a version tag is being set, appended `-aarch64` to the value.<br>Changes occur on lines [26](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py#L26), [31](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py#L31), [34](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py#L34), [35](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py#L35), & [44](https://github.com/IFeelFine/mysql-operator/blob/aarch64/mysqloperator/controller/config.py#L44). |
| [`tag.sh`](https://github.com/IFeelFine/mysql-operator/blob/aarch64/tag.sh) | Set default suffix to `aarch64`.

## Building

To build the image, we use the new file Dockerfile.patch as the basis of the build.
The Dockerfile retrieves the fully compiled operator image, and then copies our new version of the `mysqloperator` code.

Build the image:

```shell
$ podman build \
    -t ghcr.io/ifeelfine/community-operator:8.3.0-2.1.2-aarch64
    --label "org.opencontainers.image.source=https://github.com/ifeelfine/mysql-operator" \
    --label "org.opencontainers.image.description=MySQL Operator image hardcoded to aarch64 achitecture" \
    --label "org.opencontainers.image.licenses=MIT"
```