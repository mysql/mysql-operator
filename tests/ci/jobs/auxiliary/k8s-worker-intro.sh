#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

# introduction info and deleting existing clusters for a given k8s environment
case "${K8S_DRIVER}" in
minikube)
	minikube version
	minikube profile list
	minikube delete --all
	;;
k3d)
	k3d version
	k3d cluster list
	k3d cluster delete --all
	;;
kind)
	kind version
	kind get clusters
	kind delete clusters --all
	kubectl config get-contexts
	;;
*)
	echo "fatal error: unknown k8s environment ${K8S_DRIVER}!"
	exit 100
	;;
esac
